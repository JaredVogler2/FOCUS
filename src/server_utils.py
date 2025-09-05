# src/server_utils.py

import platform
import subprocess
import socket
import time
import sys

def kill_port(port=5000):
    system = platform.system()
    try:
        if system == 'Windows':
            command = f'netstat -ano | findstr :{port}'
            result = subprocess.run(command, shell=True, capture_output=True, text=True)
            if result.stdout:
                lines = result.stdout.strip().split('\n')
                for line in lines:
                    if f':{port}' in line and 'LISTENING' in line:
                        parts = line.split()
                        pid = parts[-1]
                        kill_command = f'taskkill /F /PID {pid}'
                        subprocess.run(kill_command, shell=True, capture_output=True)
                        print(f"Killed process {pid} using port {port}")
                        time.sleep(1)
        else:
            command = f'lsof -ti:{port}'
            result = subprocess.run(command, shell=True, capture_output=True, text=True)
            if result.stdout:
                pid = result.stdout.strip()
                kill_command = f'kill -9 {pid}'
                subprocess.run(kill_command, shell=True)
                print(f"Killed process {pid} using port {port}")
                time.sleep(1)
    except Exception as e:
        print(f"Warning: Could not auto-kill port {port}: {e}")

def check_and_kill_port(port=5000):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    result = sock.connect_ex(('127.0.0.1', port))
    sock.close()
    if result == 0:
        print(f"Port {port} is in use. Attempting to free it...")
        kill_port(port)
        time.sleep(1)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex(('127.0.0.1', port))
        sock.close()
        if result == 0:
            print(f"Failed to free port {port}. Please manually kill the process.")
            sys.exit(1)
        else:
            print(f"Port {port} successfully freed!")
