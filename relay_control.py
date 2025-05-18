import machine
import onewire
import ds18x20
import time
import ujson

CONFIG_FILE = "relay_config.json"

class RelayController:
    def __init__(self, relay_pins, ds18b20_pin):
        self.relay_pins = [machine.Pin(pin, machine.Pin.OUT, value=1) for pin in relay_pins] # value=1 for OFF initially
        self.relay_states = [False] * len(relay_pins)

        self.ds_pin = machine.Pin(ds18b20_pin)
        self.ow = onewire.OneWire(self.ds_pin)
        self.ds = ds18x20.DS18X20(self.ow)
        try:
            self.ds_sensors = self.ds.scan()
            print(f"[INIT] Found {len(self.ds_sensors)} DS18B20 sensor(s): {self.ds_sensors}")
        except onewire.OneWireError as e:
            print(f"[INIT] OneWireError scanning DS18B20: {e}")
            self.ds_sensors = []
        except Exception as e_scan:
            print(f"[INIT] General error scanning DS18B20: {e_scan}")
            self.ds_sensors = []

        self.default_settings = [{
            'mode': 'MANUAL',
            'low': 22.0,
            'high': 26.0,
            'hyst': 0.5,
            'sensor_index': 0,
            'lock': False # If True, prevents turning relay ON (both manual and auto)
        } for _ in relay_pins]

        self.settings = [s.copy() for s in self.default_settings]
        # Initialize relays to OFF state using the new set_relay logic
        # Load settings first, then set initial state based on them (though default is OFF)
        self.load_settings_from_file()
        for i in range(len(relay_pins)):
            self.set_relay(i, False, force=True) # Force initial OFF state, bypassing lock for init

    def load_settings_from_file(self):
        try:
            with open(CONFIG_FILE, 'r') as f:
                loaded = ujson.load(f)
                if isinstance(loaded, list) and len(loaded) == len(self.settings):
                    for i, s_loaded in enumerate(loaded):
                        for key, default_val in self.default_settings[i].items():
                            if key in s_loaded:
                                try:
                                    if isinstance(default_val, float): self.settings[i][key] = float(s_loaded[key])
                                    elif isinstance(default_val, int): self.settings[i][key] = int(s_loaded[key])
                                    elif isinstance(default_val, bool): # For 'lock'
                                        # Ensure 'lock' from JSON is correctly bool
                                        if isinstance(s_loaded[key], str):
                                            self.settings[i][key] = s_loaded[key].lower() in ('true', '1', 'yes')
                                        else:
                                            self.settings[i][key] = bool(s_loaded[key])
                                    else: self.settings[i][key] = s_loaded[key] # For 'mode'
                                except ValueError:
                                    print(f"[LOAD] Type error for key {key} in relay {i}, using default.")
                                    self.settings[i][key] = default_val
                            else: self.settings[i][key] = default_val
                    print("[LOAD] Settings loaded from file")
                else:
                    print("[LOAD] Config file format/length error. Using defaults.")
                    self.settings = [s.copy() for s in self.default_settings]
                    self.save_settings_to_file()
        except OSError:
            print("[LOAD] Config file not found. Using defaults and creating.")
            self.settings = [s.copy() for s in self.default_settings]
            self.save_settings_to_file()
        except Exception as e:
            print(f"[LOAD] Failed to load settings: {e}. Using defaults.")
            self.settings = [s.copy() for s in self.default_settings]
            self.save_settings_to_file()

    def save_settings_to_file(self):
        try:
            with open(CONFIG_FILE, 'w') as f:
                ujson.dump(self.settings, f)
            print("[SAVE] Settings saved to file")
        except Exception as e:
            print(f"[SAVE ERROR] Could not save settings: {e}")

    def read_temperatures(self):
        temps = {}
        if not self.ds_sensors:
            # print("[TEMP_READ] No DS18B20 sensors in self.ds_sensors list.")
            return temps
        try:
            self.ds.convert_temp()
            time.sleep_ms(750)
            for i, rom in enumerate(self.ds_sensors):
                try:
                    t = self.ds.read_temp(rom)
                    if t is not None and t != 85.0 and t != -127.0:
                        temps[i] = round(t, 2)
                    # else: print(f"[TEMP_READ] Invalid temp from sensor {i}")
                except Exception as e_read_single:
                    print(f"[TEMP_READ_ERROR] Sensor {i}: {e_read_single}")
        except onewire.OneWireError as e_ow:
             print(f"[TEMP_READ_ONEWIRE_ERROR] {e_ow}")
        except Exception as e_general:
            print(f"[TEMP_READ_GENERAL_ERROR] {e_general}")
        return temps

    def control_relays_by_temp(self):
        temps = self.read_temperatures()
        max_sensor_idx = len(self.ds_sensors) - 1 if self.ds_sensors else -1

        for i, setting in enumerate(self.settings):
            if setting['mode'].upper() != 'AUTO':
                continue

            si = int(setting.get('sensor_index', 0))
            if not self.ds_sensors:
                # print(f"[AUTO_CTRL {i}] No sensors.")
                continue
            if not (0 <= si <= max_sensor_idx):
                print(f"[AUTO_CTRL {i}] Invalid sensor index {si}.")
                continue

            temp = temps.get(si)
            if temp is None:
                # print(f"[AUTO_CTRL {i}] Temp for sensor {si} is None.")
                continue

            try:
                low = float(setting.get('low', self.default_settings[i]['low']))
                high = float(setting.get('high', self.default_settings[i]['high']))
                hyst = float(setting.get('hyst', self.default_settings[i]['hyst']))
                # 'lock' is read by set_relay directly from self.settings[i]['lock']
            except ValueError:
                print(f"[AUTO_CTRL {i}] Type error in settings.")
                continue

            current_pin_state_is_on = self.relay_states[i] # True if ON, False if OFF

            # Heater logic: ON if temp <= low-hyst, OFF if temp >= high+hyst
            if temp <= (low - hyst):
                if not current_pin_state_is_on: # If currently OFF, try to turn ON
                    print(f"[AUTO_CTRL {i}] Condition to turn ON: Temp={temp:.2f} <= {low - hyst:.2f}")
                    self.set_relay(i, True) # Lock check is inside set_relay
            elif temp >= (high + hyst):
                if current_pin_state_is_on: # If currently ON, try to turn OFF
                    print(f"[AUTO_CTRL {i}] Condition to turn OFF: Temp={temp:.2f} >= {high + hyst:.2f}")
                    # For AUTO OFF, we want to bypass the lock, as lock only prevents turning ON.
                    self.set_relay(i, False, force=True)
            # else:
                # print(f"[AUTO_CTRL {i}] Temp {temp:.2f} is between {low-hyst:.2f} and {high+hyst:.2f}. No change.")
        return temps

    def set_relay(self, index, state, force=False):
        """
        Sets the relay state.
        :param index: Index of the relay.
        :param state: True for ON, False for OFF.
        :param force: If True, bypasses the lock when turning ON. Lock never prevents turning OFF.
        """
        if not (0 <= index < len(self.relay_pins)):
            print(f"[SET_RELAY_ERROR] Invalid relay index: {index}")
            return

        is_locked = self.settings[index].get('lock', False)
        current_state_is_on = self.relay_states[index]

        if state is True: # Attempting to turn ON
            if current_state_is_on and not force: # Already ON, no change unless forced
                # print(f"[SET_RELAY {index}] Already ON.")
                return
            if is_locked and not force:
                print(f"[SET_RELAY {index}] Blocked from turning ON by lock.")
                return
            # Proceed to turn ON
            self.relay_pins[index].value(0) # 0 for ON (active low)
            self.relay_states[index] = True
            print(f"[SET_RELAY {index}] Turned ON.")
        else: # Attempting to turn OFF (state is False)
            if not current_state_is_on: # Already OFF, no change
                # print(f"[SET_RELAY {index}] Already OFF.")
                return
            # Proceed to turn OFF (lock does not prevent turning OFF)
            self.relay_pins[index].value(1) # 1 for OFF (active low)
            self.relay_states[index] = False
            print(f"[SET_RELAY {index}] Turned OFF.")

    def toggle_relay(self, index):
        if not (0 <= index < len(self.relay_pins)):
            print(f"[TOGGLE_ERROR] Invalid relay index: {index}")
            return

        current_state_is_on = self.relay_states[index]
        desired_new_state_is_on = not current_state_is_on

        self.settings[index]['mode'] = 'MANUAL' # Toggling implies manual override

        # The set_relay method will handle the lock check if attempting to turn ON
        self.set_relay(index, desired_new_state_is_on)
        # No need to print here, set_relay does it.

        self.save_settings_to_file() # Save changed mode and potentially lock status if GUI updates it

    def get_relay_states(self):
        return self.relay_states