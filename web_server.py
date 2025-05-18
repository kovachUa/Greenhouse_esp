import socket
import gc
import ure
import ujson

DEFAULT_PORT = 12345

def start_web_server(controller, port=DEFAULT_PORT):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    
    try:
        s.bind(('0.0.0.0', port))
    except OSError as e:
        print(f"Error binding to port {port}: {e}")
        return

    s.listen(2)
    print(f"Web server listening on port {port}")

    while True:
        cl = None
        cl_file = None
        response_sent = False # Moved here for broader scope in finally
        try:
            cl, addr = s.accept()
            # print(f"Accepted connection from {addr[0]}:{addr[1]}")
            cl_file = cl.makefile('rwb', 0)
            request_line_bytes = cl_file.readline()

            if not request_line_bytes:
                cl_file.close(); cl.close(); gc.collect(); continue

            try:
                request_line = request_line_bytes.decode('utf-8').strip()
                parts = request_line.split()
                if len(parts) < 2:
                    cl.send(b"HTTP/1.0 400 Bad Request\r\n\r\nMalformed Request")
                    response_sent = True # Mark response as sent
                    cl_file.close(); cl.close(); gc.collect(); continue
                method = parts[0]
                path = parts[1]
            except UnicodeDecodeError:
                cl.send(b"HTTP/1.0 400 Bad Request\r\n\r\nNon-UTF8 Request")
                response_sent = True # Mark response as sent
                cl_file.close(); cl.close(); gc.collect(); continue
            
            # print(f"[WEB_SERVER] Request: {method} {path}") # Log all requests

            while True:
                line = cl_file.readline()
                if not line or line == b'\r\n': break

            if method == "GET":
                if path.startswith("/toggle"):
                    match = ure.search(r"i=(\d+)", path)
                    if match:
                        idx = int(match.group(1))
                        controller.toggle_relay(idx) 
                        cl.send(b"HTTP/1.0 302 Found\r\nLocation: /\r\nContent-Length: 0\r\n\r\n")
                        response_sent = True
                    else:
                        cl.send(b"HTTP/1.0 400 Bad Request\r\n\r\nMissing index 'i' for toggle.")
                        response_sent = True
                elif path.startswith("/set"):
                    query_match = ure.search(r"\?(.*)", path)
                    if query_match:
                        query = query_match.group(1)
                        params = {}
                        for pair in query.split('&'):
                            if '=' in pair:
                                k, v = pair.split('=', 1)
                                params[k] = v
                        try:
                            i = int(params.get("i", "-1"))
                            if 0 <= i < len(controller.settings):
                                s_cfg = controller.settings[i]
                                default_s = controller.default_settings[i]
                                s_cfg['low'] = float(params.get('on', s_cfg.get('low', default_s['low'])))
                                s_cfg['high'] = float(params.get('off', s_cfg.get('high', default_s['high'])))
                                s_cfg['mode'] = params.get('mode', s_cfg.get('mode', default_s['mode'])).upper()
                                s_cfg['sensor_index'] = int(params.get('sensor', s_cfg.get('sensor_index', default_s['sensor_index'])))
                                s_cfg['hyst'] = float(params.get('hyst', s_cfg.get('hyst', default_s['hyst'])))
                                s_cfg['lock'] = params.get('lock', '0') == '1'
                                controller.save_settings_to_file()
                                print(f"[SET] Settings updated for relay {i}: {s_cfg}")
                                cl.send(b"HTTP/1.0 302 Found\r\nLocation: /\r\nContent-Length: 0\r\n\r\n")
                                response_sent = True
                            else:
                                cl.send(b"HTTP/1.0 400 Bad Request\r\n\r\nInvalid relay index 'i'.")
                                response_sent = True
                        except ValueError as e_val:
                            print(f"[SET PARAMS ERROR] {e_val}")
                            cl.send(b"HTTP/1.0 400 Bad Request\r\n\r\nInvalid parameter value.")
                            response_sent = True
                        except Exception as e_set:
                            print(f"[SET ERROR] {e_set}")
                            cl.send(b"HTTP/1.0 500 Internal Server Error\r\n\r\nError processing set request.")
                            response_sent = True
                    else:
                        cl.send(b"HTTP/1.0 400 Bad Request\r\n\r\nMissing parameters for set.")
                        response_sent = True

                elif path == "/api/get_all_status":
                    print("[DEBUG_WEB_API] Entering /api/get_all_status handler.")
                    temps = controller.control_relays_by_temp() # This now has more debug prints
                    relays_current_states = controller.get_relay_states()
                    
                    status = {
                        "temperatures": temps, # temps comes from control_relays_by_temp()
                        "num_sensors_detected": len(controller.ds_sensors), # Get current count
                        "relays": []
                    }
                    print(f"[DEBUG_WEB_API] Temps from controller: {temps}")
                    print(f"[DEBUG_WEB_API] Num sensors from controller.ds_sensors: {len(controller.ds_sensors)}")

                    for i_relay in range(len(controller.settings)):
                        setting = controller.settings[i_relay]
                        relay_on_off_state = relays_current_states[i_relay] if i_relay < len(relays_current_states) else False
                        status["relays"].append({
                            "state": relay_on_off_state,
                            "mode": setting.get("mode", controller.default_settings[i_relay]["mode"]),
                            "low": setting.get("low", controller.default_settings[i_relay]["low"]),
                            "high": setting.get("high", controller.default_settings[i_relay]["high"]),
                            "sensor_index": setting.get("sensor_index", controller.default_settings[i_relay]["sensor_index"]),
                            "hyst": setting.get("hyst", controller.default_settings[i_relay]["hyst"]),
                            "lock": setting.get("lock", controller.default_settings[i_relay]["lock"])
                        })
                    
                    print(f"[DEBUG_WEB_API] Status object constructed before sending: {status}")
                    js = ""
                    try:
                        js = ujson.dumps(status)
                    except Exception as e_json_dump:
                        print(f"[DEBUG_WEB_API_ERROR] Failed to dump status to JSON: {e_json_dump}")
                        # Send an error response if JSON dumping fails
                        cl.send(b"HTTP/1.0 500 Internal Server Error\r\n\r\nJSON DUMP ERROR")
                        response_sent = True # Mark as sent
                        # Don't proceed to send js if it's empty or caused an error
                    
                    if not response_sent: # Only send if no error during JSON dump
                        js_bytes = js.encode('utf-8')
                        cl.send(b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n")
                        cl.send(f"Content-Length: {len(js_bytes)}\r\n".encode('utf-8'))
                        cl.send(b"\r\n") 
                        cl.send(js_bytes)
                        print("[DEBUG_WEB_API] Sent /api/get_all_status response.")
                        response_sent = True
                
                elif path == "/": 
                    ok_message = b"ESP8266 Relay Controller OK."
                    cl.send(b"HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\n")
                    cl.send(f"Content-Length: {len(ok_message)}\r\n".encode('utf-8'))
                    cl.send(b"\r\n")
                    cl.send(ok_message)
                    response_sent = True

            if not response_sent: 
                not_found_msg = b"Resource not found."
                cl.send(b"HTTP/1.0 404 Not Found\r\nContent-Type: text/plain\r\n")
                cl.send(f"Content-Length: {len(not_found_msg)}\r\n".encode('utf-8'))
                cl.send(b"\r\n")
                cl.send(not_found_msg)
                response_sent = True # Mark as sent
                print(f"[WEB_SERVER] Path not found: {path if 'path' in locals() else 'N/A'}")

        except OSError as e: 
            print(f"[WEB_SERVER_OSError]: {e}") # e.g. ECONNRESET, ETIMEDOUT
            # Response might not have been sent or client disconnected
        except Exception as e_main_loop:
            print(f"[WEB_SERVER_GeneralError_In_Loop]: {e_main_loop}")
            import sys
            sys.print_exception(e_main_loop) # More detailed traceback for general errors
            if cl and not response_sent: # Try to send 500 if connection still seems open
                try:
                    err_msg = b"Internal Server Error"
                    cl.send(b"HTTP/1.0 500 Internal Server Error\r\nContent-Type: text/plain\r\n")
                    cl.send(f"Content-Length: {len(err_msg)}\r\n".encode('utf-8'))
                    cl.send(b"\r\n")
                    cl.send(err_msg)
                except Exception as e_send_500:
                    print(f"Error sending 500 response: {e_send_500}")
        finally:
            if cl_file:
                try: cl_file.close()
                except: pass
            if cl:
                try: cl.close()
                except: pass
            gc.collect()