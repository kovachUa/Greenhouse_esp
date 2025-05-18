# main.py
import gc
import time
import network
from relay_control import RelayController
from web_server import start_web_server

# --- Піни ---
RELAY_PINS = [5, 4, 0, 2]  # GPIO для 4 реле (D1, D2, D3, D4 on NodeMCU)
TEMP_PIN = 14              # GPIO для DS18B20 (D5 on NodeMCU)

# --- Wi-Fi ---
# ЗАМІНІТЬ НА ВАШІ ДАНІ! / REPLACE WITH YOUR CREDENTIALS!
SSID = '****'
PASSWORD = '****'
# ---------------------------------------------------------

def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        print(f'Connecting to Wi-Fi (SSID: {SSID})...')
        wlan.connect(SSID, PASSWORD)
        connect_attempts = 0
        max_attempts = 20 
        while not wlan.isconnected() and connect_attempts < max_attempts:
            print('.', end='')
            time.sleep(1)
            connect_attempts += 1
        print() 

    if wlan.isconnected():
        print('Network config:', wlan.ifconfig())
        return wlan.ifconfig()[0]
    else:
        print(f'Failed to connect to Wi-Fi after {max_attempts} seconds.')
        return None

def main():
    print("Starting ESP8266 Relay Controller")
    gc.collect()
    ip_address = connect_wifi()

    if not ip_address:
        print("Could not connect to WiFi. Halting.")
        return

    print(f"ESP8266 is available at IP: {ip_address} on port 12345")
    
    controller = RelayController(RELAY_PINS, TEMP_PIN)
    start_web_server(controller, port=12345)

if __name__ == '__main__':
    main()
