from PyQt5 import QtCore, QtGui, QtWidgets
import os
import subprocess
from uuid import uuid1
from random_username.generate import generate_username
import minecraft_launcher_lib
from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtWidgets import QApplication, QComboBox, QStyledItemDelegate, QVBoxLayout, QWidget, QLineEdit
from PyQt5.QtCore import Qt, QPoint
import json
import time
from PyQt5.QtGui import QStandardItem, QStandardItemModel
import psutil
from asyncqt import QEventLoop
import asyncio
import requests
from urllib.parse import urlparse, parse_qs, unquote
from PyQt5.QtWidgets import QApplication, QMainWindow
from PyQt5.QtCore import QUrl, pyqtSlot
from PyQt5.QtWebEngineWidgets import QWebEngineView
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

class CenterDelegate(QStyledItemDelegate):
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.displayAlignment = Qt.AlignCenter

class MicrosoftAuthenticationException(Exception):
    pass

class AuthTokens:
    def __init__(self, access_token, refresh_token):
        self.access_token = access_token
        self.refresh_token = refresh_token

class MicrosoftAuthenticator:
    AUTH_URL = "https://login.live.com/oauth20_authorize.srf"
    TOKEN_URL = "https://login.live.com/oauth20_token.srf"
    MINECRAFT_URLS = {
        "auth": "https://api.minecraftservices.com/authentication/login_with_xbox",
        "store": "https://api.minecraftservices.com/entitlements/mcstore",
        "profile": "https://api.minecraftservices.com/minecraft/profile"
    }
    CLIENT_ID = "000000004C12AE6F"
    SCOPE = "service::user.auth.xboxlive.com::MBI_SSL"

    def __init__(self):
        self.session = requests.Session()

    async def login_with_webview(self):
        try:
            tokens = self.extract_tokens(await LoginFrame().start(self.get_auth_url()))
            return await self.authenticate(tokens)
        except MicrosoftAuthenticationException as e:
            print("Authentication failed:", e)

    async def authenticate(self, tokens):
        xbox_token = self.xbox_login(tokens.access_token)
        xsts_token, user_hash = self.xsts_login(xbox_token)
        mc_token = self.mc_login(user_hash, xsts_token)
        profile = self.get_mc_profile(mc_token) if self.has_entitlement(mc_token) else None
        self.save_to_json(mc_token, profile)
        return {"profile": profile, "access_token": mc_token, "refresh_token": tokens.refresh_token}
 
    def xbox_login(self, token):
        data = {"Properties": {"AuthMethod": "RPS", "SiteName": "user.auth.xboxlive.com", "RpsTicket": token},
                "RelyingParty": "http://auth.xboxlive.com", "TokenType": "JWT"}
        return self.session.post("https://user.auth.xboxlive.com/user/authenticate", json=data).json()['Token']
    
    def xsts_login(self, xbox_token):
        data = {"Properties": {"SandboxId": "RETAIL", "UserTokens": [xbox_token]},
                "RelyingParty": "rp://api.minecraftservices.com/", "TokenType": "JWT"}
        response = self.session.post("https://xsts.auth.xboxlive.com/xsts/authorize", json=data).json()
        return response['Token'], response['DisplayClaims']['xui'][0]['uhs']

    def mc_login(self, user_hash, xsts_token):
        data = {"identityToken": f"XBL3.0 x={user_hash};{xsts_token}"}
        return self.session.post(self.MINECRAFT_URLS['auth'], json=data).json()['access_token']

    def has_entitlement(self, token):
        headers = {"Authorization": f"Bearer {token}"}
        items = self.session.get(self.MINECRAFT_URLS['store'], headers=headers).json().get('items', [])
        return any(item.get('name') == "game_minecraft" for item in items)

    def get_mc_profile(self, token):
        headers = {"Authorization": f"Bearer {token}"}
        return self.session.get(self.MINECRAFT_URLS['profile'], headers=headers).json()

    def extract_tokens(self, url):
        params = parse_qs(urlparse(url).fragment)
        access_token = params.get("access_token", [None])[0]
        refresh_token = params.get("refresh_token", [None])[0]
        if not access_token or not refresh_token:
            raise MicrosoftAuthenticationException("Invalid tokens")
        return AuthTokens(unquote(access_token), unquote(refresh_token))

    def get_auth_url(self):
        return f"{self.AUTH_URL}?client_id={self.CLIENT_ID}&redirect_uri=https://login.live.com/oauth20_desktop.srf&scope={self.SCOPE}&response_type=token"

    def save_to_json(self, mc_token, profile):
        if profile:
            user_data = {
                "access_token": mc_token,
                "uuid": profile.get("id"),
                "username": profile.get("name")
            }
            with open('auth_data.json', 'w') as f:
                json.dump(user_data, f, indent=4)
            print("Saved data to auth_data.json")

class LoginFrame(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Microsoft Authentication")
        self.setGeometry(100, 100, 750, 750)
        self.web_view = QWebEngineView(self)
        self.setCentralWidget(self.web_view)
        self.future = asyncio.Future()
        self.web_view.page().loadFinished.connect(self.override_user_agent)
        self.web_view.urlChanged.connect(self.check_url)

    def override_user_agent(self):
        js_code = """
            Object.defineProperty(navigator, 'userAgent', {
                get: () => "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/60.0.3112.113 Safari/537.36"
            });
        """
        self.web_view.page().runJavaScript(js_code)

    async def start(self, url):
        self.show()
        self.web_view.setUrl(QUrl(url))
        return await self.future

    @pyqtSlot("QUrl")
    def check_url(self, url):
        if "access_token" in url.toString():
            self.hide()
            if not self.future.done():
                self.future.set_result(url.toString())

    def closeEvent(self, event):
        if not self.future.done():
            self.future.set_exception(MicrosoftAuthenticationException("User closed the authentication window"))
        event.accept()

class JavawFinder:
    def __init__(self, thread_count=5):
        self.thread_count = thread_count

    def find_javaw(self):
        try:
            result = subprocess.run(
                ["where", "javaw.exe"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=True
            )
            
            if result.returncode == 0:
                return result.stdout.strip()
            else:
                return None
        except Exception as e:
            return f"An error occurred: {e}"

    def find_javaw_multithreaded(self):
        with ThreadPoolExecutor(max_workers=self.thread_count) as executor:
            futures = [executor.submit(self.find_javaw) for _ in range(self.thread_count)]
            for future in as_completed(futures):
                result = future.result()
                if result:
                    return result
        return "javaw.exe not found. Ensure Java is installed and added to PATH."

class LaunchThread(QtCore.QThread):
    launch_setup_signal = QtCore.pyqtSignal(str, str, QLineEdit, bool)  
    progress_update_signal = QtCore.pyqtSignal(int, int, str)
    state_update_signal = QtCore.pyqtSignal(bool)
    stop_signal = QtCore.pyqtSignal()

    def __init__(self):
        super().__init__()
        self.version_id = ''
        self.username = ''
        self.progress = 0
        self.progress_max = 0
        self.progress_label = ''
        self.stopping = False

        self.launch_setup_signal.connect(self.launch_setup)
        self.stop_signal.connect(self.stop_launch)

    def launch_setup(self, version_id, username, nicknameEdit, IsLicense):
        self.version_id = version_id
        self.username = username
        self.IsLicense=IsLicense
        self.stopping = False
        self.nicknameEdit=nicknameEdit

    def update_progress_label(self, value):
        self.progress_label = value
        self.progress_update_signal.emit(self.progress, self.progress_max, self.progress_label)

    def update_progress(self, value):
        self.progress = value
        self.progress_update_signal.emit(self.progress, self.progress_max, self.progress_label)

    def update_progress_max(self, value):
        self.progress_max = value
        self.progress_update_signal.emit(self.progress, self.progress_max, self.progress_label)

    def stop_launch(self):
        self.stopping = True
        self.terminate()

    def update_fullscreen_option(self):
        settings_file_path = 'settings_data.json'

        if os.path.exists(settings_file_path):
            with open(settings_file_path, 'r') as json_file:
                settings_data = json.load(json_file)

            fullscreen_value = settings_data.get("fullscreen", None)
            
            if fullscreen_value is None:
                print("fullscreen option not found in JSON file.")
                return

            roaming_directory = os.path.join(os.getenv('APPDATA'), '.unixlauncher')
            options_file_path = os.path.join(roaming_directory, 'options.txt')

            if os.path.exists(options_file_path):
                with open(options_file_path, 'r') as file:
                    lines = file.readlines()

                updated = False
                with open(options_file_path, 'w') as file:
                    for line in lines:
                        if 'fullscreen:' in line:
                            file.write(f'fullscreen:{str(fullscreen_value).lower()}\n')
                            updated = True
                        else:
                            file.write(line)

                if updated:
                    print(f"fullscreen mode set to {fullscreen_value}")
                else:
                    print("fullscreen option not found in options.txt")
            else:
                print(f"File {options_file_path} doesn't exist.")
        else:
            print(f"JSON settings file {settings_file_path} doesn't exist.")

    def run(self):
        javaw_finder = JavawFinder(thread_count=1)
        path_to_javaw = javaw_finder.find_javaw_multithreaded()

        if os.path.exists('settings_data.json'):
            with open('settings_data.json', 'r') as f:
                self.settingsData = json.load(f)

        else:
            print("settings isn't configured, using defaults")
            settings = {
                "memory": 2048,
                "jvmArguments": ['-Xmx2G', '-Xms2G'],
                "java_path": path_to_javaw.replace("\\", "/"),
                "show_releases": False,
                "show_beta": False,
                "show_snapshots": False,
                "show_alpha": False,
                "resolutionWidth": 1280,
                "resolutionHeight": 720,
                "fullscreen": False,
            }
            try:
                with open('settings_data.json', 'w') as f:
                    json.dump(settings, f, indent=4)
                self.settingsData = settings
            except Exception as e:
                print(f"Error saving settings: {e}")
                self.settingsData = {}

        minecraft_version = self.version_id
        minecraft_directory = minecraft_launcher_lib.utils.get_minecraft_directory().replace('minecraft', 'unixlauncher')
        self.state_update_signal.emit(True)
        try:
            minecraft_launcher_lib.install.install_minecraft_version(
                versionid=minecraft_version,
                minecraft_directory=minecraft_directory,
                callback={
                    'setStatus': self.update_progress_label,
                    'setProgress': self.update_progress,
                    'setMax': self.update_progress_max
                }
            )

            if not self.username:
                self.username = generate_username()[0]

            if self.IsLicense:
                with open('auth_data.json', 'r', encoding='utf-8') as file:
                    license_data = json.load(file)
                options = {
                    'username': license_data.get("username"),
                    'uuid': license_data.get("uuid"),
                    'token': license_data.get("access_token"),
                    'jvmArguments': self.settingsData.get("jvmArguments"),
                    'executablePath': self.settingsData.get("java_path"),
                    'customResolution': True,
                    'resolutionWidth': str(self.settingsData.get("resolutionWidth", 1280)),
                    'resolutionHeight': str(self.settingsData.get("resolutionHeight", 720))
                }
            else:
                options = {
                    'username': self.username,
                    'uuid': str(uuid1()),
                    'token': "",
                    'jvmArguments': self.settingsData.get("jvmArguments"),
                    'executablePath': self.settingsData.get("java_path"),
                    'customResolution': True,
                    'resolutionWidth': str(self.settingsData.get("resolutionWidth", 1280)),
                    'resolutionHeight': str(self.settingsData.get("resolutionHeight", 720))
                }

            self.update_fullscreen_option()

            command = minecraft_launcher_lib.command.get_minecraft_command(
                version=self.version_id,
                minecraft_directory=minecraft_directory,
                options=options
            ) 

            if not self.stopping:
                subprocess.Popen(command, creationflags=subprocess.CREATE_NO_WINDOW)

        except Exception as e:
            print("Error during Minecraft launch:")
            print(f"Exception: {str(e)}")
            print("Traceback:")
            traceback.print_exc()

        finally:
            self.state_update_signal.emit(False)

class SettingsWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super(SettingsWindow, self).__init__()
        self.authenticator = MicrosoftAuthenticator()
        self.loop = QEventLoop()
        asyncio.set_event_loop(self.loop)
        self.auth_data_file = "auth_data.json"     
        javaw_finder = JavawFinder(thread_count=1)
        path_to_javaw = javaw_finder.find_javaw_multithreaded()

        if os.path.exists('settings_data.json'):
            with open('settings_data.json', 'r') as f:
                self.settingsData = json.load(f)

        else:
            print("settings isn't configured, using defaults")
            settings = {
                "memory": 2048,
                "jvmArguments": ['-Xmx2G', '-Xms2G'],
                "java_path": path_to_javaw.replace("\\", "/"),
                "show_releases": False,
                "show_beta": False,
                "show_snapshots": False,
                "show_alpha": False,
                "resolutionWidth": 1280,
                "resolutionHeight": 720,
                "fullscreen": False,
            }
            try:
                with open('settings_data.json', 'w') as f:
                    json.dump(settings, f, indent=4)
                self.settingsData = settings
            except Exception as e:
                print(f"Error saving settings: {e}")
                self.settingsData = {}

        self.setObjectName("MainWindow")
        self.resize(413, 167)
        self.setStyleSheet("background-color: rgb(41, 46, 49);")
        self.setWindowTitle("Settings")
        self.setMinimumSize(QtCore.QSize(413, 167))
        self.setMaximumSize(QtCore.QSize(413, 167))

        icon_path = "assets/Icon.png"
        icon = QtGui.QIcon(icon_path)
        self.setWindowIcon(icon)
        self.centralwidget = QtWidgets.QWidget(self)
        self.centralwidget.setObjectName("centralwidget")

        self.Memory_Label = QtWidgets.QLabel(self.centralwidget)
        self.Memory_Label.setGeometry(QtCore.QRect(20, 20, 47, 13))
        self.Memory_Label.setStyleSheet("color: white;")
        self.Memory_Label.setObjectName("Memory_Label")

        self.MemorySlider = QtWidgets.QSlider(self.centralwidget)
        self.MemorySlider.setGeometry(QtCore.QRect(70, 17, 160, 21))
        self.MemorySlider.setOrientation(QtCore.Qt.Horizontal)
        self.MemorySlider.setObjectName("MemorySlider")
        self.MemorySlider.setMinimum(1024)
        max_memory_mb = psutil.virtual_memory().total // (1024 * 1024)
        self.MemorySlider.setMaximum(max_memory_mb)
        self.MemorySlider.setTickPosition(QtWidgets.QSlider.TicksBelow)
        self.MemorySlider.setTickInterval(1024)
        self.MemorySlider.setPageStep(1024)
        self.predefined_values = [1024 * i for i in range(1, (max_memory_mb // 1024) + 1)]
        self.MemorySlider.valueChanged.connect(self.update_memory_stat)

        self.MemoryStat = QtWidgets.QLabel(self.centralwidget)
        self.MemoryStat.setGeometry(QtCore.QRect(240, 20, 47, 13))
        self.MemoryStat.setStyleSheet("color: white;")
        self.MemoryStat.setObjectName("MemoryStat")
        self.MemoryStat.setText(f"{self.MemorySlider.value()}MB")

        self.PathToJava = QtWidgets.QLineEdit(self.centralwidget)
        self.PathToJava.setGeometry(QtCore.QRect(75, 47, 201, 20))
        self.PathToJava.setStyleSheet(
            "background: transparent;"
            "border-radius: 7px;"
            "border: 2px solid white;"
            "color: white;"
            "font: 63 10pt 'Bahnschrift SemiBold';"
            "text-align: center;"
        )
        self.PathToJava.setObjectName("PathToJava")
        self.PathToJava.textChanged.connect(self.update_pathtojavaexe)

        self.JavaPath_Label = QtWidgets.QLabel(self.centralwidget)
        self.JavaPath_Label.setGeometry(QtCore.QRect(20, 50, 47, 13))
        self.JavaPath_Label.setStyleSheet("color: white;")
        self.JavaPath_Label.setObjectName("JavaPath_Label")

        self.Releases = QtWidgets.QCheckBox(self.centralwidget)
        self.Releases.setGeometry(QtCore.QRect(20, 115, 101, 17))
        self.Releases.setStyleSheet("color: white;")
        self.Releases.setObjectName("Releases")
        self.Releases.stateChanged.connect(self.update_versionStates)

        self.BetaVersions = QtWidgets.QCheckBox(self.centralwidget)
        self.BetaVersions.setGeometry(QtCore.QRect(20, 140, 121, 17))
        self.BetaVersions.setStyleSheet("color: white;")
        self.BetaVersions.setObjectName("BetaVersions")
        self.BetaVersions.stateChanged.connect(self.update_versionStates)

        self.Snapshots = QtWidgets.QCheckBox(self.centralwidget)
        self.Snapshots.setGeometry(QtCore.QRect(150, 115, 121, 17))
        self.Snapshots.setStyleSheet("color: white;")
        self.Snapshots.setObjectName("Snapshots")
        self.Snapshots.stateChanged.connect(self.update_versionStates)

        self.resolutionLine1 = QtWidgets.QLineEdit(self.centralwidget)
        self.resolutionLine1.setGeometry(QtCore.QRect(280, 115, 51, 20))
        self.resolutionLine1.setStyleSheet("                background: transparent;\n"
"                border-radius: 7px;\n"
"                border: 2px solid white;\n"
"                color: white;\n"
"                font: 63 10pt \"Bahnschrift SemiBold\";\n"
"                text-align: center;")
        self.resolutionLine1.setObjectName("resolutionLine1")

        self.xLabel = QtWidgets.QLabel(self.centralwidget)
        self.xLabel.setGeometry(QtCore.QRect(338, 113, 10, 20))
        self.xLabel.setStyleSheet("color: white")
        self.xLabel.setObjectName("xLabel")

        self.resolutionLine2 = QtWidgets.QLineEdit(self.centralwidget)
        self.resolutionLine2.setGeometry(QtCore.QRect(350, 115, 51, 20))
        self.resolutionLine2.setStyleSheet("                background: transparent;\n"
"                border-radius: 7px;\n"
"                border: 2px solid white;\n"
"                color: white;\n"
"                font: 63 10pt \"Bahnschrift SemiBold\";\n"
"                text-align: center;")
        self.resolutionLine2.setObjectName("resolutionLine2")
        
        self.AlphaVersions = QtWidgets.QCheckBox(self.centralwidget)
        self.AlphaVersions.setGeometry(QtCore.QRect(150, 140, 131, 17))
        self.AlphaVersions.setStyleSheet("color: white;")
        self.AlphaVersions.setObjectName("AlphaVersions")
        self.AlphaVersions.stateChanged.connect(self.update_versionStates)
        
        self.FullScreenCheckBox = QtWidgets.QCheckBox(self.centralwidget)
        self.FullScreenCheckBox.setGeometry(QtCore.QRect(283, 140, 131, 17))
        self.FullScreenCheckBox.setStyleSheet("color: white;")
        self.FullScreenCheckBox.setObjectName("FullScreenCheckBox")

        self.LicenseProfile = QtWidgets.QCheckBox(self.centralwidget)
        self.LicenseProfile.setGeometry(QtCore.QRect(300, 20, 101, 17))
        self.LicenseProfile.setStyleSheet("color: white;")
        self.LicenseProfile.setObjectName("LicenseProfile")
        self.LicenseProfile.stateChanged.connect(self.ProfileChangerHandle)

        self.CrackedProfile = QtWidgets.QCheckBox(self.centralwidget)
        self.CrackedProfile.setGeometry(QtCore.QRect(300, 50, 101, 17))
        self.CrackedProfile.setStyleSheet("color: white;")
        self.CrackedProfile.setObjectName("CrackedProfile")
        self.CrackedProfile.stateChanged.connect(self.ProfileChangerHandle)

        if os.path.exists("auth_data.json"):
            self.LicenseProfile.setChecked(True)
            self.LicenseProfile.setEnabled(False)
            self.CrackedProfile.setChecked(False)
        else:
            self.LicenseProfile.setChecked(False)
            self.LicenseProfile.setEnabled(True)
            self.CrackedProfile.setChecked(True)

        self.SelectJavaExeButton = QtWidgets.QPushButton(self.centralwidget)
        self.SelectJavaExeButton.setGeometry(QtCore.QRect(20, 80, 381, 23))
        self.SelectJavaExeButton.setStyleSheet(
            "QPushButton { background-color: rgba(70, 173, 226, 1); border-radius: 7px; font: 63 8pt 'Bahnschrift SemiBold'; color: white; }"
            "QPushButton:Hover { background-color: rgba(56, 139, 181, 1) }"
        )
        self.SelectJavaExeButton.setObjectName("SelectJavaExeButton")
        self.SelectJavaExeButton.clicked.connect(self.select_java_exe)

        self.setCentralWidget(self.centralwidget)
        self.Memory_Label.setText("Memory")
        self.JavaPath_Label.setText("Java Exe")
        self.Releases.setText("Show Releases")
        self.BetaVersions.setText("Show Beta Versions")
        self.Snapshots.setText("Show Snapshots")
        self.AlphaVersions.setText("Show Alpha Versions")
        self.LicenseProfile.setText("License Profile")
        self.CrackedProfile.setText("Cracked Profile")
        self.xLabel.setText("x")
        self.FullScreenCheckBox.setText("Launch In Fullscreen")
        self.MemoryStat.setText(f"{self.MemorySlider.value()}MB")
        self.SelectJavaExeButton.setText("Select Java Exe")
        validator = QtGui.QIntValidator()
        self.resolutionLine1.setValidator(validator)
        self.resolutionLine2.setValidator(validator)
        self.resolutionLine1.textChanged.connect(self.update_resolution_width)
        self.resolutionLine2.textChanged.connect(self.update_resolution_height)
        self.FullScreenCheckBox.stateChanged.connect(self.save_settings)

        self.load_settings()

    def save_settings(self):
        settings = {
            "memory": self.MemorySlider.value(),
            "jvmArguments": self.jvm_args,
            "java_path": self.PathToJava.text(),
            "show_releases": not self.Releases.isChecked(),
            "show_beta": not self.BetaVersions.isChecked(),
            "show_snapshots": not self.Snapshots.isChecked(),
            "show_alpha": not self.AlphaVersions.isChecked(),
            "resolutionWidth": int(self.resolutionLine1.text()) if self.resolutionLine1.text().isdigit() else 1280,
            "resolutionHeight": int(self.resolutionLine2.text()) if self.resolutionLine2.text().isdigit() else 720,
            "fullscreen": self.FullScreenCheckBox.isChecked(),
        }
        try:
            with open('settings_data.json', 'w') as f:
                json.dump(settings, f, indent=4)
        except Exception as e:
            print(f"Error saving settings: {e}")

    def load_settings(self):
        try:
            if os.path.exists('settings_data.json'):
                with open('settings_data.json', 'r') as f:
                    settings = json.load(f)
                self.MemorySlider.setValue(settings.get('memory', 512))
                self.PathToJava.setText(settings.get('java_path', ''))
                self.Releases.setChecked(not settings.get('show_releases', True))
                self.BetaVersions.setChecked(not settings.get('show_beta', True))
                self.Snapshots.setChecked(not settings.get('show_snapshots', True))
                self.AlphaVersions.setChecked(not settings.get('show_alpha', True))
                self.resolutionLine1.setText(str(settings.get("resolutionWidth", 1280)))
                self.resolutionLine2.setText(str(settings.get("resolutionHeight", 720)))
                self.FullScreenCheckBox.setChecked(settings.get("fullscreen", False))
        except Exception as e:
            print(f"Error loading settings: {e}")

    def update_resolution_width(self, text):
        if text.isdigit():
            self.save_settings()

    def update_resolution_height(self, text):
        if text.isdigit():
            self.save_settings()

    def update_memory_stat(self):
        memory_value = self.MemorySlider.value()
        closest_value = min(self.predefined_values, key=lambda x: abs(x - memory_value))
        self.MemorySlider.setValue(closest_value)

        if closest_value >= 1024:
            self.MemoryStat.setText(f"{closest_value // 1024}GB")
        else:
            self.MemoryStat.setText(f"{closest_value}MB")

        self.update_jvm_args(closest_value)
        print(self.jvm_args)
        self.save_settings()

    def update_jvm_args(self, memory_value):
        if memory_value >= 1024:
            memory_in_gb = memory_value // 1024
            self.jvm_args = [f"-Xmx{memory_in_gb}G", f"-Xms{memory_in_gb}G"]
        else:
            self.jvm_args = [f"-Xmx{memory_value}M", f"-Xms{memory_value}M"]
        self.save_settings()

    def update_pathtojavaexe(self):
        self.current_path=self.PathToJava.text()
        self.save_settings()
    
    def update_versionStates(self):
        checkbox_map = {
            self.Releases: "ShowReleases",
            self.BetaVersions: "ShowBetaVersions",
            self.Snapshots: "ShowSnapshots",
            self.AlphaVersions: "ShowAlphaVersions"
        }

        any_checked = False

        for checkbox, attribute_name in checkbox_map.items():
            if checkbox.isChecked():
                setattr(self, attribute_name, False)
                any_checked = True
            else:
                setattr(self, attribute_name, True)

        if not any_checked:
            for attribute_name in checkbox_map.values():
                setattr(self, attribute_name, True)
        self.save_settings()

    def select_java_exe(self):
        options = QtWidgets.QFileDialog.Options()
        file, _ = QtWidgets.QFileDialog.getOpenFileName(
            None, "Select a File", "", "Java file (javaw.exe)", options=options
        )
        if file:
            self.PathToJava.setText(file)
        self.save_settings()
    
    def start_login(self):
        asyncio.create_task(self.perform_login())

    async def perform_login(self):
        try:
            result = await self.authenticator.login_with_webview()
            if result:
                print("Login successful")
        except MicrosoftAuthenticationException as e:
            print("Authentication failed:", e)

    def delete_auth_data_file(self):
        if os.path.exists(self.auth_data_file):
            try:
                os.remove(self.auth_data_file)
                print(f"{self.auth_data_file} has been deleted.")
            except Exception as e:
                print(f"Error deleting {self.auth_data_file}: {e}")
        else:
            print(f"{self.auth_data_file} does not exist.")

    def ProfileChangerHandle(self):
        license_checked = self.LicenseProfile.isChecked()
        cracked_checked = self.CrackedProfile.isChecked()
        license_enabled = self.LicenseProfile.isEnabled()

        if license_checked and not cracked_checked:
            # Enable License Profile
            self.LicenseProfile.setDisabled(True)
            self.start_login()
        elif not license_enabled and cracked_checked:
            # Enable Cracked Profile
            self.LicenseProfile.setChecked(False)
            self.LicenseProfile.setDisabled(False)
            self.delete_auth_data_file()
        elif license_checked and cracked_checked:
            # Force License Profile
            self.LicenseProfile.setChecked(True)
            self.LicenseProfile.setEnabled(False)
            self.CrackedProfile.setChecked(False)

class Ui_MainWindow(object):
    def setupUi(self, MainWindow):
        model = QStandardItemModel()
        minecraft_directory = minecraft_launcher_lib.utils.get_minecraft_directory().replace('minecraft', 'unixlauncher')
        self.timer = QTimer()
        self.is_dragging = False
        self.drag_start_pos = None
        self.launch_thread = LaunchThread()
        saved_username = self.load_username()

        def LicenseStateTask():
            while True:
                self.IsLicense = self.check_license()
                time.sleep(1)
                if self.IsLicense:
                    print("License mode enabled. Using auth_data.json.")
                    if os.path.exists("auth_data.json"):
                        with open('auth_data.json', 'r', encoding='utf-8') as file:
                            self.license_data = json.load(file)
                        self.nicknameEdit.setText(self.license_data.get("username"))
                        self.nicknameEdit.setDisabled(True)
                    else:
                        print("file not found")
                        self.load_username()
                        self.nicknameEdit.setDisabled(False)
                else:
                    """i need to put something here or else that thing will give error :D"""

        thread = threading.Thread(target=LicenseStateTask)
        thread.start()

        MainWindow.setWindowTitle("Unix Launcher")
        MainWindow.setObjectName("MainWindow")
        MainWindow.resize(980, 538)
        MainWindow.setMinimumSize(QtCore.QSize(980, 538))
        MainWindow.setMaximumSize(QtCore.QSize(980, 538))
        MainWindow.setStyleSheet("background-color: rgb(41, 46, 49);")
        MainWindow.setUnifiedTitleAndToolBarOnMac(False)
        MainWindow.setWindowFlags(Qt.FramelessWindowHint)
        self.centralwidget = QtWidgets.QWidget(MainWindow)
        self.centralwidget.setObjectName("centralwidget")
        WindowIcon_path = "assets/Icon.png"
        WindowIcon = QtGui.QIcon(WindowIcon_path)
        MainWindow.setWindowIcon(WindowIcon)

        self.TopbarBG = QtWidgets.QLabel(self.centralwidget)
        self.TopbarBG.setGeometry(QtCore.QRect(0, 0, 980, 56))
        self.TopbarBG.setStyleSheet("background-color: rgba(33, 36, 41, 1)")
        self.TopbarBG.setText("")
        self.TopbarBG.setObjectName("TopbarBG")
        self.TopbarBG.mousePressEvent = self.mousePressEvent
        self.TopbarBG.mouseMoveEvent = self.mouseMoveEvent
        self.TopbarBG.mouseReleaseEvent = self.mouseReleaseEvent

        self.Logo = QtWidgets.QLabel(self.centralwidget)
        self.Logo.setGeometry(QtCore.QRect(10, 10, 306, 37))
        self.Logo.setStyleSheet("background: transparent;")
        self.Logo.setText("")
        self.Logo.setPixmap(QtGui.QPixmap("assets/Logo.png"))
        self.Logo.setObjectName("Logo")

        self.closeButton = QtWidgets.QPushButton(self.centralwidget)
        self.closeButton.setGeometry(QtCore.QRect(931, 8, 40, 40))
        self.closeButton.setStyleSheet("background: transparent;")
        self.closeButton.setText("")
        self.closeButton.setIconSize(QtCore.QSize(40, 40))
        self.closeButton.setFlat(False)
        self.closeButton.setObjectName("closeButton")
        self.closeButton.enterEvent=self.CloseButtonEnterEvent
        self.closeButton.leaveEvent=self.CloseButtonLeaveEvent
        self.closeButton.clicked.connect(self.close_window)
        closeButtonIcon = QtGui.QIcon()
        closeButtonIcon.addPixmap(QtGui.QPixmap("assets/CloseButton.png"), QtGui.QIcon.Normal, QtGui.QIcon.Off)
        self.closeButton.setIcon(closeButtonIcon)

        self.collapseButton = QtWidgets.QPushButton(self.centralwidget)
        self.collapseButton.setGeometry(QtCore.QRect(891, 26, 24, 4))
        self.collapseButton.setStyleSheet("background: transparent;")
        self.collapseButton.setText("")
        
        self.collapseButton.setIconSize(QtCore.QSize(24, 4))
        self.collapseButton.setObjectName("collapseButton")
        self.collapseButton.enterEvent=self.CollapseButtonEnterEvent
        self.collapseButton.leaveEvent=self.CollapseButtonLeaveEvent
        self.collapseButton.clicked.connect(self.minimize_window) 
        collapseButtonIcon = QtGui.QIcon()
        collapseButtonIcon.addPixmap(QtGui.QPixmap("assets/CollapseButton.png"), QtGui.QIcon.Normal, QtGui.QIcon.Off)
        self.collapseButton.setIcon(collapseButtonIcon)

        self.playButton = QtWidgets.QPushButton(self.centralwidget)
        self.playButton.setGeometry(QtCore.QRect(708, 468, 246, 38))
        self.playButton.setStyleSheet("""
        QPushButton {
            background-color: rgba(70, 173, 226, 1);
            border-radius: 7px;
            font: 63 20pt "Bahnschrift SemiBold";
            color: white;
        }

        QPushButton:Hover {
            background-color: rgba(56, 139, 181, 1)
        }
        """)
        self.playButton.setIconSize(QtCore.QSize(16, 16))
        self.playButton.setObjectName("playButton")
        self.playButton.clicked.connect(self.launch_game)

        self.folderButton = QtWidgets.QPushButton(self.centralwidget)
        self.folderButton.setGeometry(QtCore.QRect(948, 509, 22, 22))
        self.folderButton.setStyleSheet("background: transparent;")
        self.folderButton.setText("")
        self.folderButton.setIconSize(QtCore.QSize(22, 22))
        self.folderButton.setObjectName("folderButton")
        self.folderButton.enterEvent=self.folderButtonEnterEvent
        self.folderButton.leaveEvent=self.folderButtonLeaveEvent
        self.folderButton.clicked.connect(self.open_directory)
        folderButtonIcon = QtGui.QIcon()
        folderButtonIcon.addPixmap(QtGui.QPixmap("assets/FolderButton.png"), QtGui.QIcon.Normal, QtGui.QIcon.Off)
        self.folderButton.setIcon(folderButtonIcon)

        self.settingsButton = QtWidgets.QPushButton(self.centralwidget)
        self.settingsButton.setGeometry(QtCore.QRect(923, 509, 22, 22))
        self.settingsButton.setStyleSheet("background: transparent;")
        self.settingsButton.setText("")
        self.settingsButton.setIconSize(QtCore.QSize(22, 22))
        self.settingsButton.setObjectName("settingsButton")
        self.settingsButton.enterEvent=self.settingsButtonEnterEvent
        self.settingsButton.leaveEvent=self.settingsButtonLeaveEvent
        self.settingsButton.clicked.connect(self.open_settings)
        settingsButtonIcon = QtGui.QIcon()
        settingsButtonIcon.addPixmap(QtGui.QPixmap("assets/settingsButton.png"), QtGui.QIcon.Normal, QtGui.QIcon.Off)
        self.settingsButton.setIcon(settingsButtonIcon)

        self.GrayBG = QtWidgets.QLabel(self.centralwidget)
        self.GrayBG.setGeometry(QtCore.QRect(686, 60, 290, 474))
        self.GrayBG.setFocusPolicy(QtCore.Qt.NoFocus)
        self.GrayBG.setStyleSheet("""
        QLabel{
            background-color: rgba(35, 39, 43, 1);
            border-radius: 9px;
        }
        """)
        self.GrayBG.setText("")
        self.GrayBG.setObjectName("GrayBG")

        self.stopButton = QtWidgets.QPushButton(self.centralwidget)
        self.stopButton.setEnabled(True)
        self.stopButton.setGeometry(QtCore.QRect(708, 468, 246, 38))
        self.stopButton.setStyleSheet("""
        QPushButton {
            background-color: rgba(226, 70, 70, 1);
            border-radius: 7px;
            font: 63 20pt "Bahnschrift SemiBold";
            color: white;
        }

        QPushButton:Hover {
            background-color:rgba(157, 54, 54, 1)
        }
        """)
        self.stopButton.setIconSize(QtCore.QSize(16, 16))
        self.stopButton.setObjectName("stopButton")
        self.stopButton.setVisible(False)
        self.stopButton.clicked.connect(self.stop_game)

        self.previewImage = QtWidgets.QLabel(self.centralwidget)
        self.previewImage.setGeometry(QtCore.QRect(705, 70, 256, 144))
        self.previewImage.setStyleSheet("background: transparent;")
        self.previewImage.setText("")
        self.previewImage.setPixmap(QtGui.QPixmap("assets/PreviewImg_1.png"))
        self.previewImage.setObjectName("previewImage")

        self.nicknameEdit = QtWidgets.QLineEdit(self.centralwidget)
        self.nicknameEdit.setGeometry(QtCore.QRect(708, 420, 246, 38))
        self.nicknameEdit.setStyleSheet("""
        QLineEdit {
            background-color: rgba(255, 255, 255, 0);
            border-radius: 7px;
            border: 2px solid white;
            color: white;
            font: 63 12pt "Bahnschrift SemiBold";
            text-align: center;
        }
        """)
        self.nicknameEdit.setAlignment(QtCore.Qt.AlignCenter)
        self.nicknameEdit.setObjectName("nicknameEdit")

        self.versionSelectBox = QtWidgets.QComboBox(self.centralwidget)
        self.versionSelectBox.setGeometry(QtCore.QRect(708, 370, 246, 38))
        self.versionSelectBox.setStyleSheet("""
        QComboBox {
                background: transparent;
                border-radius: 7px;
                border: 2px solid white;
                color: white;
                font: 63 12pt "Bahnschrift SemiBold";
                text-align: center;
                padding: 5px;
        }
        QComboBox::drop-down {
            background: transparent;
            border: none;
        }
        QComboBox::down-arrow {
            width: 14px;
            height: 14px;
        }
        QComboBox::down-arrow:on { 
            top: 1px;
        }
        QAbstractItemView {
            background: rgba(44, 44, 44, 1);
            border: none;
            color: white;
            selection-background-color: #555555;
            selection-color: white;
            border-radius: 5px;
            padding: 5px;
        }
        QScrollBar:vertical {
            border: none;
            background: #2f2f2f;
            width: 12px;
            margin: 0px;
            border-radius: 6px;
        }
        QScrollBar::handle:vertical {
            background-color: #888888;
            min-height: 20px;
            border-radius: 6px;
        }
        QScrollBar::handle:vertical:hover {
            background-color: #555555;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            background: none;
        }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
            background: none;
        }
        """)
        self.versionSelectBox.setEditable(True)
        line_edit = self.versionSelectBox.lineEdit()
        line_edit.setAlignment(Qt.AlignCenter)
        line_edit.setReadOnly(True)
        delegate = CenterDelegate(self.versionSelectBox)
        self.versionSelectBox.setItemDelegate(delegate)
        self.versionSelectBox.setObjectName("versionSelectBox")

        for version in minecraft_launcher_lib.utils.get_version_list():
            self.versionSelectBox.addItem(version["id"])
            item = QStandardItem(version["id"])
            version_directory = os.path.join(minecraft_directory, 'versions', version["id"])
            if os.path.exists(version_directory): item.setForeground(QtGui.QColor("white"))
            else: item.setForeground(QtGui.QColor("gray"))
            model.appendRow(item)
        self.versionSelectBox.setModel(model)

        self.versionInfo = QtWidgets.QLabel(self.centralwidget)
        self.versionInfo.setGeometry(QtCore.QRect(705, 220, 256, 31))
        self.versionInfo.setStyleSheet("""
        QLabel{
            background-color: rgba(255, 255, 255, 0);
            color: white;
            font: 63 20pt "Bahnschrift SemiBold";
            text-align: center;
        }
        """)
        self.versionInfo.setAlignment(QtCore.Qt.AlignCenter)
        self.versionInfo.setObjectName("versionInfo")
        self.versionInfo.setText(self.versionSelectBox.currentText())

        self.loaderInfo = QtWidgets.QLabel(self.centralwidget)
        self.loaderInfo.setGeometry(QtCore.QRect(705, 250, 256, 21))
        self.loaderInfo.setStyleSheet("""
        QLabel{
            background-color: rgba(255, 255, 255, 0);
            color: white;
            font: 63 14pt "Bahnschrift SemiBold";
            text-align: center;
        }
        """)
        self.loaderInfo.setAlignment(QtCore.Qt.AlignCenter)
        self.loaderInfo.setObjectName("loaderInfo")
        self.loaderInfo.setText("Loader: Vanilla")

        self.progressBar = QtWidgets.QProgressBar(self.centralwidget)
        self.progressBar.setGeometry(QtCore.QRect(708, 320, 246, 38))
        self.progressBar.setStyleSheet("""
        QProgressBar {
                background: transparent;
                border-radius: 7px;
                border: 2px solid white;
                color: white;
                font: 63 12pt "Bahnschrift SemiBold";
                text-align: center;
                padding: 4px;
        }
        QProgressBar::chunk {
                background-color: #2196F3;
                border-radius: 7px; 
        }
        """)
        self.progressBar.setProperty("value", 0)
        self.progressBar.setObjectName("progressBar")
        self.progressBar.setVisible(False)

        self.warningLabel=QtWidgets.QLabel(self.centralwidget)
        self.warningLabel.setGeometry(QtCore.QRect(20, 50, 300, 60))
        self.warningLabel.setText("Warning! Loader select are just placeholder for now.")
        self.warningLabel.setStyleSheet("color: white;")

        self.forgeButton = QtWidgets.QPushButton(self.centralwidget)
        self.forgeButton.setGeometry(QtCore.QRect(20, 100, 100, 40))
        self.forgeButton.setStyleSheet("""
        QPushButton {
            background: transparent;
            color: white;
            border: 2px solid white;
            border-radius: 9px;
            padding: 5px;
            font: 63 14pt "Bahnschrift SemiBold";
        }
            QPushButton:hover {
            background-color: #555;
        }
        """)
        self.forgeButton.setText("Forge")
        self.forgeButton.setObjectName("forgeButton")
        self.forgeButton.clicked.connect(self.select_forge)

        self.quiltButton = QtWidgets.QPushButton(self.centralwidget)
        self.quiltButton.setGeometry(QtCore.QRect(20, 150, 100, 40))
        self.quiltButton.setStyleSheet("""
        QPushButton {
            background: transparent;
            color: white;
            border: 2px solid white;
            border-radius: 9px;
            padding: 5px;
            font: 63 14pt "Bahnschrift SemiBold";
        }
            QPushButton:hover {
            background-color: #555;
        }
        """)
        self.quiltButton.setText("Quilt")
        self.quiltButton.setObjectName("quiltButton")
        self.quiltButton.clicked.connect(self.select_quilt)

        self.fabricButton = QtWidgets.QPushButton(self.centralwidget)
        self.fabricButton.setGeometry(QtCore.QRect(20, 200, 100, 40))
        self.fabricButton.setStyleSheet("""
        QPushButton {
            background: transparent;
            color: white;
            border: 2px solid white;
            border-radius: 9px;
            padding: 5px;
            font: 63 14pt "Bahnschrift SemiBold";
        }
            QPushButton:hover {
            background-color: #555;
        }
        """)
        self.fabricButton.setText("Fabric")
        self.fabricButton.setObjectName("fabricButton")
        self.fabricButton.clicked.connect(self.select_fabric)

        self.VanillaButton = QtWidgets.QPushButton(self.centralwidget)
        self.VanillaButton.setGeometry(QtCore.QRect(20, 250, 100, 40))
        self.VanillaButton.setStyleSheet("""
        QPushButton {
            background: transparent;
            color: white;
            border: 2px solid white;
            border-radius: 9px;
            padding: 5px;
            font: 63 14pt "Bahnschrift SemiBold";
        }
            QPushButton:hover {
            background-color: #555;
        }
        """)
        self.VanillaButton.setText("Vanilla")
        self.VanillaButton.setObjectName("VanillaButton")
        self.VanillaButton.clicked.connect(self.select_Vanilla)

        self.dropdownarrowicon = QtWidgets.QLabel(self.centralwidget)
        self.dropdownarrowicon.setGeometry(QtCore.QRect(935, 374, 16, 31))
        self.dropdownarrowicon.setText("")
        self.dropdownarrowicon.setPixmap(QtGui.QPixmap("assets/arrow_down.png"))
        self.dropdownarrowicon.setStyleSheet("background: transparent;")
        self.dropdownarrowicon.setAlignment(QtCore.Qt.AlignCenter)
        self.dropdownarrowicon.setObjectName("dropdownarrowicon")

        self.GrayBG.raise_()
        self.TopbarBG.raise_()
        self.Logo.raise_()
        self.dropdownarrowicon.raise_()
        self.closeButton.raise_()
        self.VanillaButton.raise_()
        self.forgeButton.raise_()
        self.quiltButton.raise_()
        self.fabricButton.raise_()
        self.collapseButton.raise_()
        self.playButton.raise_()
        self.folderButton.raise_()
        self.stopButton.raise_()
        self.previewImage.raise_()
        self.nicknameEdit.raise_()
        self.versionSelectBox.raise_()
        self.versionInfo.raise_()
        self.loaderInfo.raise_()
        self.progressBar.raise_()
        self.playButton.setText("Play")
        self.stopButton.setText("Stop")
        self.settingsButton.raise_()
        self.nicknameEdit.setPlaceholderText("Enter nickname")
        MainWindow.setCentralWidget(self.centralwidget)

        self.launch_thread.progress_update_signal.connect(self.update_progress)
        self.launch_thread.state_update_signal.connect(self.state_update)

        self.create_unixlauncher_directory()      
        self.versionSelectBox.currentIndexChanged.connect(self.update_version_info)        

        self.timer.timeout.connect(self.hide_progress_bar)

        if saved_username:
            self.nicknameEdit.setText(saved_username)

    def check_license(self):
        return os.path.exists("auth_data.json")

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.is_dragging = True
            self.drag_start_pos = event.globalPos() - MainWindow.frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self.is_dragging:
            MainWindow.move(event.globalPos() - self.drag_start_pos)

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            self.is_dragging = False

    def hide_progress_bar(self):
        self.progressBar.setVisible(False)

    def save_username(self, username):
        with open("saved_username.txt", "w") as file:
            file.write(username)

    def load_username(self):
        if os.path.exists("saved_username.txt"):
            with open("saved_username.txt", "r") as file:
                return file.read().strip()
        return None

    def launch_game(self):
        self.progressBar.setVisible(True)
        username = self.nicknameEdit.text()
        if username:
            self.save_username(username)
        self.launch_thread.launch_setup_signal.emit(self.versionSelectBox.currentText(), username, self.nicknameEdit, self.IsLicense)
        self.launch_thread.start()
        self.stopButton.setVisible(True)
        self.playButton.setDisabled(True)

    def state_update(self, value):
        self.playButton.setDisabled(value)
        self.playButton.setEnabled(not value)
        self.stopButton.setVisible(value)
        self.progressBar.setVisible(value)
        if not value and self.progressBar.value() == 100:
            self.progressBar.setVisible(False)

    def update_progress(self, progress, max_progress, label):
        if max_progress > 0:
            percentage = int(progress / max_progress * 100)
        else:
            percentage = 0
        self.progressBar.setValue(progress)
        self.progressBar.setMaximum(max_progress)
        text = f"{percentage}%"
        self.progressBar.setFormat(text)

    def open_directory(self):
        roaming_directory = os.path.join(os.getenv('APPDATA'), '.unixlauncher')
        if os.path.exists(roaming_directory):
            subprocess.Popen(['explorer', roaming_directory])
        else:
            QtWidgets.QMessageBox

    def create_unixlauncher_directory(self):
        roaming_directory = os.path.join(os.getenv('APPDATA'), '.unixlauncher')
        if not os.path.exists(roaming_directory):
            os.makedirs(roaming_directory)

    def update_version_info(self):
        selected_version = self.versionSelectBox.currentText()
        self.versionInfo.setText(selected_version)

    def select_forge(self):
        self.loaderInfo.setText('Loader: Forge')

    def select_quilt(self):
        self.loaderInfo.setText('Loader: Quilt')

    def select_fabric(self):
        self.loaderInfo.setText('Loader: Fabric')

    def select_Vanilla(self):
        self.loaderInfo.setText('Loader: Vanilla')

    def close_window(self):
        os._exit(0)

    def minimize_window(self):
        MainWindow.showMinimized()
            
    def stop_game(self):
        self.progressBar.setVisible(False)
        self.stopButton.setVisible(False)
        self.playButton.setEnabled(True)
        self.launch_thread.stop_signal.emit()

    def CloseButtonEnterEvent(self, event):
        self.closeButton.setIcon(QtGui.QIcon("assets/CloseButtonHover.png"))

    def CloseButtonLeaveEvent(self, event):
        self.closeButton.setIcon(QtGui.QIcon("assets/CloseButton.png"))

    def CollapseButtonEnterEvent(self, event):
        self.collapseButton.setIcon(QtGui.QIcon("assets/CollapseButtonHover.png"))

    def CollapseButtonLeaveEvent(self, event):
        self.collapseButton.setIcon(QtGui.QIcon("assets/CollapseButton.png"))

    def folderButtonEnterEvent(self, event):
        self.folderButton.setIcon(QtGui.QIcon("assets/FolderButtonHover.png"))

    def folderButtonLeaveEvent(self, event):
        self.folderButton.setIcon(QtGui.QIcon("assets/FolderButton.png"))
        
    def settingsButtonEnterEvent(self, event):
        self.settingsButton.setIcon(QtGui.QIcon("assets/settingsButtonHover.png"))

    def settingsButtonLeaveEvent(self, event):
        self.settingsButton.setIcon(QtGui.QIcon("assets/settingsButton.png"))

    def open_settings(self):
        self.settings_window = SettingsWindow()
        self.settings_window.show()

if __name__ == "__main__":
    import sys
    app = QtWidgets.QApplication(sys.argv)
    MainWindow = QtWidgets.QMainWindow()
    ui = Ui_MainWindow()
    ui.setupUi(MainWindow)
    MainWindow.show()
    loop = QEventLoop(app)
    asyncio.set_event_loop(loop)
    with loop:
        os._exit(loop.run_forever())
    sys.exit(os._exit(0))