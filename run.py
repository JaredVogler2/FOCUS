import os
import threading
# import webbrowser

from src.app import create_app
from src import server_utils

# def open_browser():
#     webbrowser.open_new("http://127.0.0.1:5000/")

if __name__ == '__main__':
    app = create_app()
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        server_utils.check_and_kill_port(5000)
        # threading.Timer(1.25, open_browser).start()
    app.run(debug=True, host='0.0.0.0', port=5000)
