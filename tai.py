import sys
import re
import os
import requests
import json
from urllib.request import urlopen
import shutil
import subprocess

from PySide6.QtWidgets import (
    QStyledItemDelegate, QStyle, QStyleOptionViewItem,  
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QListView, QLabel, QMessageBox,
    QTabWidget, QLineEdit, QMenu, QAbstractItemView, QProgressDialog,
    QTextBrowser, QSizePolicy
)
from PySide6.QtGui import QFont, QIcon


from PySide6.QtCore import (
    Qt, QThread, QObject, Signal, Slot, QUrl, QProcess,
    QAbstractListModel, QModelIndex, QSize, QEvent
)
from PySide6.QtGui import (
    QFontMetrics, QDesktopServices, QPixmap, QPainter, QFont
)
from qt_material import apply_stylesheet
import qtawesome as qta

ICON_CACHE_DIR = "./icon_cache"


def ensure_icon_cache():
    if not os.path.exists(ICON_CACHE_DIR):
        os.makedirs(ICON_CACHE_DIR)

def download_icon(url):
    ensure_icon_cache()
    icon_filename = os.path.join(ICON_CACHE_DIR, os.path.basename(url))
    if not os.path.exists(icon_filename):
        try:
            response = requests.get(url)
            with open(icon_filename, 'wb') as icon_file:
                icon_file.write(response.content)
        except Exception as e:
            print(f"Failed to download icon: {e}")
            return None
    return icon_filename

def strip_ansi_escape_codes(text):
    """
    Remove ANSI escape codes from text.
    """
    ansi_escape = re.compile(r'\x1B\[[0-?]*[ -/]*[@-~]')
    return ansi_escape.sub('', text)

def make_links_clickable(text):
    """
    Convert URLs in text to clickable hyperlinks.
    """
    url_pattern = re.compile(r'(https?://\S+)')
    return url_pattern.sub(r'<a href="\1">\1</a>', text)

class AppListModel(QAbstractListModel):
    """
    Custom model to hold app data for QListView.
    Each app is represented as a tuple: (app_name, description, is_app)
    """
    def __init__(self, apps=None):
        super().__init__()
        self.apps = apps or []

    def data(self, index, role):
        if not index.isValid():
            return None

        try:
            app_name, description, is_app = self.apps[index.row()]
        except ValueError as e:
            print(f"Error unpacking app data: {e}")
            return None

        if role == Qt.DisplayRole:
            return app_name
        elif role == Qt.ToolTipRole:
            return description
        elif role == Qt.UserRole:
            return app_name
        elif role == Qt.UserRole + 1:
            return is_app  # Indicates if the item is an app
        return None

    def rowCount(self, parent=QModelIndex()):
        return len(self.apps)

class AppListDelegate(QStyledItemDelegate):
    """
    Custom delegate to render items in QListView.
    """
    def paint(self, painter, option, index):
        app_name = index.data(Qt.DisplayRole)
        description = index.model().apps[index.row()][1]

        painter.save()

        # Draw background if selected
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())

        # Draw app name
        painter.setPen(option.palette.text().color())
        font = option.font
        font.setBold(True)
        font.setPointSize(max(10, option.font.pointSize()))  # Ensure font size is valid
        painter.setFont(font)
        rect = option.rect.adjusted(5, 5, -5, -5)
        painter.drawText(rect.x(), rect.y(), rect.width(), 20, Qt.AlignLeft | Qt.AlignVCenter, app_name)

        # Draw description
        font.setBold(False)
        font.setPointSize(max(8, option.font.pointSize() - 1))  # Ensure font size is valid
        painter.setFont(font)
        painter.setPen(Qt.gray)
        painter.drawText(rect.x(), rect.y() + 22, rect.width(), 18, Qt.AlignLeft | Qt.AlignVCenter, description)

        painter.restore()

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), 50)

class SuggestedAppDelegate(QStyledItemDelegate):
    app_clicked = Signal(QModelIndex)  # Custom signal for item click

    def __init__(self, parent=None):
        super().__init__(parent)

    def paint(self, painter, option, index):
        app_name = index.data(Qt.DisplayRole)
        description = index.model().apps[index.row()].get('description', '')
        icon = index.data(Qt.DecorationRole)

        painter.save()
        if option.state & QStyle.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())

        rect = option.rect.adjusted(5, 5, -5, -5)
        if icon:
            pixmap = icon.pixmap(64, 64)
            painter.drawPixmap(rect.x(), rect.y(), 64, 64, pixmap)

        painter.setPen(option.palette.text().color())
        font = option.font
        font.setBold(True)
        font.setPointSize(max(10, option.font.pointSize()))
        painter.setFont(font)
        text_rect = rect.adjusted(70, 0, 0, -20)
        painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignTop, app_name)

        font.setBold(False)
        font.setPointSize(max(8, option.font.pointSize() - 1))
        painter.setFont(font)
        painter.setPen(Qt.gray)
        desc_rect = rect.adjusted(70, 20, 0, 0)
        painter.drawText(desc_rect, Qt.AlignLeft | Qt.AlignTop, description)

        painter.restore()

    def sizeHint(self, option, index):
        return QSize(option.rect.width(), 80)

    def editorEvent(self, event, model, option, index):
        if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            self.app_clicked.emit(index)
        return super().editorEvent(event, model, option, index)

class SuggestedAppModel(QAbstractListModel):
    def __init__(self, apps, parent=None):
        super().__init__(parent)
        self.apps = apps

    def data(self, index, role):
        if role == Qt.DisplayRole:
            return self.apps[index.row()]["app_name"]
        elif role == Qt.DecorationRole:
            icon_url = self.apps[index.row()]["icon_url"]
            icon_path = download_icon(icon_url)
            if icon_path:
                return QIcon(icon_path)
            else:
                return QIcon()
        elif role == Qt.ToolTipRole:
            return self.apps[index.row()]["description"]

    def rowCount(self, parent=QModelIndex()):
        # Return the number of suggested apps in the model
        return len(self.apps)

class Worker(QObject):
    """
    Worker class to run external scripts asynchronously using QProcess.
    """
    output_ready = Signal(str)
    error_occurred = Signal(str)
    finished = Signal()

    def __init__(self, cmd):
        super().__init__()
        self.cmd = cmd
        self.process = None
        self.output_data = ''
        self.error_data = ''

    def run(self):
        script_path = "appman"
        args = [script_path] + self.cmd

        self.process = QProcess()
        self.process.setProgram(args[0])
        self.process.setArguments(args[1:])
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self.handle_stdout)
        self.process.readyReadStandardError.connect(self.handle_stderr)
        self.process.finished.connect(self.process_finished)
        self.process.start()

    def handle_stdout(self):
        data = bytes(self.process.readAllStandardOutput()).decode(errors='replace')
        output = strip_ansi_escape_codes(data)
        self.output_data += output

    def handle_stderr(self):
        data = bytes(self.process.readAllStandardError()).decode(errors='replace')
        error_output = strip_ansi_escape_codes(data)
        self.error_data += error_output

    def process_finished(self, exit_code, exit_status):
        if self.error_data:
            self.error_occurred.emit(self.error_data)
        else:
            self.output_ready.emit(self.output_data)
        self.finished.emit()

class FileLoaderWorker(QObject):
    """
    Worker class to load app list from a file asynchronously.
    """
    apps_loaded = Signal(list)

    def __init__(self, file_path):
        super().__init__()
        self.file_path = file_path

    def run(self):
        apps = []
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    app_name = self.extract_app_name(line)
                    description = self.extract_app_description(line)
                    is_app = True  # Assume entries in the file are apps
                    apps.append((app_name, description, is_app))
        except Exception as e:
            print(f"Error loading apps from file: {e}")
        self.apps_loaded.emit(apps)

    def extract_app_name(self, app_line):
        """
        Extracts the app name from a line of file.
        """
        if ' : ' in app_line:
            app_name = app_line.split(' : ', 1)[0].strip().lstrip('◆').strip()
        else:
            app_name = app_line.strip().lstrip('◆').strip()
        app_name = app_name.strip()
        return app_name

    def extract_app_description(self, app_line):
        """
        Extracts the app description from a line of file.
        """
        if ' : ' in app_line:
            description = app_line.split(' : ', 1)[1].strip()
            return description
        else:
            return ''

class AppImageManagerGUI(QMainWindow):
    """
    Main GUI class for the AppImage Manager.
    """
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AppImage Manager")
        self.setGeometry(100, 100, 960, 640)

        self.available_apps = []
        self.all_available_apps = []  # Stores all apps loaded from file
        self.available_apps_loaded = False  # Flag to check if apps have been loaded
        self.threads = []  # Keep references to threads
        self.workers = []  # Keep references to workers
        self.init_ui()

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout()

        self.tabs = QTabWidget()

        # Installed Applications Tab
        installed_tab = QWidget()
        installed_layout = QVBoxLayout()

        self.app_list_view = QListView()
        self.app_list_view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.app_list_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.app_list_view.customContextMenuRequested.connect(self.show_installed_context_menu)
        self.app_list_view.activated.connect(self.update_appimage)  # Enter key triggers update
        self.app_list_view.setItemDelegate(AppListDelegate())

        # Buttons for Installed Applications Tab
        button_layout = QHBoxLayout()
        self.update_button = QPushButton("Update AppImage")
        self.remove_button = QPushButton("Remove AppImage")
        self.update_all_button = QPushButton("Update All")
        self.refresh_button = QPushButton("Refresh List")

        # Set Material Icons on Buttons
        self.update_button.setIcon(qta.icon('fa.refresh'))
        self.remove_button.setIcon(qta.icon('fa.trash'))
        self.update_all_button.setIcon(qta.icon('fa.download'))
        self.refresh_button.setIcon(qta.icon('fa.repeat'))

        # Connect buttons
        self.update_button.clicked.connect(self.update_appimage)
        self.remove_button.clicked.connect(self.remove_appimage)
        self.update_all_button.clicked.connect(self.update_all_apps)
        self.refresh_button.clicked.connect(self.refresh_installed_apps)

        button_layout.addWidget(self.update_button)
        button_layout.addWidget(self.remove_button)
        button_layout.addWidget(self.update_all_button)
        button_layout.addWidget(self.refresh_button)

        # Set layout for installed applications tab
        installed_layout.addWidget(self.app_list_view)
        installed_layout.addLayout(button_layout)
        installed_tab.setLayout(installed_layout)

        # Available Applications Tab
        available_tab = QWidget()
        available_layout = QVBoxLayout()

        search_layout = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search Applications...")
        self.search_box.textChanged.connect(self.on_search_text_changed)  # Trigger search on text change
        self.search_button = QPushButton()
        self.search_button.setIcon(qta.icon('fa.search'))
        self.search_button.clicked.connect(self.perform_search)
        search_layout.addWidget(self.search_box)
        search_layout.addWidget(self.search_button)

        self.available_list_view = QListView()
        self.available_list_view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.available_list_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.available_list_view.customContextMenuRequested.connect(self.show_available_context_menu)
        self.available_list_view.activated.connect(self.install_selected_appimage)  # Enter key triggers install
        self.available_list_view.setItemDelegate(AppListDelegate())

        install_layout = QHBoxLayout()
        self.install_button = QPushButton("Install AppImage")
        self.install_button.setIcon(qta.icon('fa.download'))
        self.install_button.clicked.connect(self.install_selected_appimage)
        install_layout.addWidget(self.install_button)

        available_layout.addLayout(search_layout)
        available_layout.addWidget(self.available_list_view)
        available_layout.addLayout(install_layout)
        available_tab.setLayout(available_layout)

        # Suggested Applications Tab
        suggested_tab = QWidget()
        suggested_layout = QVBoxLayout()

        self.suggested_list_view = QListView()
        self.suggested_list_view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.suggested_list_view.setItemDelegate(SuggestedAppDelegate())
        self.suggested_list_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.suggested_list_view.customContextMenuRequested.connect(self.show_suggested_context_menu)
        
        
        self.suggested_delegate = SuggestedAppDelegate(self.suggested_list_view)
        self.suggested_list_view.setItemDelegate(self.suggested_delegate)
        self.suggested_delegate.app_clicked.connect(self.install_selected_appimage_from_suggested)
        # Install button at the bottom
        self.install_suggested_button = QPushButton("Install Selected App")
        self.install_suggested_button.setIcon(qta.icon('fa.download'))
        self.install_suggested_button.clicked.connect(self.install_selected_from_button)

        suggested_layout.addWidget(self.suggested_list_view)
        suggested_layout.addWidget(self.install_suggested_button)
        suggested_tab.setLayout(suggested_layout)

        # Info Tab
        info_tab = QWidget()
        info_layout = QVBoxLayout()

        info_label = QLabel("Welcome to TAI AppImage Manager!")
        info_label.setStyleSheet("font-weight: bold; font-size: 16px;")
        instructions = QTextBrowser()
        instructions.setOpenExternalLinks(True)
        instructions.setHtml("""
            <p style="font-size: 16px;">TAI is a user-friendly tool designed to help you efficiently manage AppImages on your system. With TAI, you can explore a curated list of applications, install AppImages, and keep them up-to-date with ease. TAI utilizes <strong>AppMan</strong> as the backend, which supports user (local) installation. This means all installations, including AppMan itself and any applications managed with TAI, are stored locally in your user environment, ensuring an organized and easily accessible experience.</p>

            <p style="font-size: 18px;"><strong>Key Features:</strong></p>
            <ul style="font-size: 16px;">
                <li><strong>Explore Apps:</strong> Browse through a collection of suggested applications from categories like Productivity, Graphics, and Media.</li>
                <li><strong>Install & Manage:</strong> Install new applications, update existing ones, and remove any AppImage with a single click.</li>
                <li><strong>Minimal Storage Impact:</strong> AppImages allow for a self-contained and portable application experience without additional dependencies, ensuring minimal storage usage and easy cleanup.</li>
                <li><strong>Stay Updated:</strong> Regularly update your installed applications to keep up with the latest features and security patches.</li>
            </ul>

            <p style="font-size: 18px;"><strong>Getting Started:</strong></p>
            <ol style="font-size: 16px;">
                <li>Navigate to the <strong>Available Applications</strong> tab to search and install AppImages of your choice.</li>
                <li>Use the <strong>Installed Applications</strong> tab to update or remove AppImages that you've installed.</li>
                <li>Visit the <strong>Suggested Applications</strong> tab for a curated selection of recommended applications to explore.</li>
            </ol>

            <p style="font-size: 16px;">For detailed documentation and support of AppMan, please visit the <a href="https://github.com/ivan-hc/AM" style="text-decoration: none;">official documentation</a>.</p>
            <p style="font-size: 16px;">For more information on TAI itself, please visit the <a href="https://github.com/iontelos/TAI" style="text-decoration: none;">our documentation</a>.</p>
            <p style="font-size: 16px;">We hope TAI makes your AppImage experience smoother and more enjoyable!</p>

        """)
        instructions.setMaximumHeight(800)
        info_layout.addWidget(info_label)
        info_layout.addWidget(instructions)
        info_tab.setLayout(info_layout)

        # Add tabs to main tab widget
        self.tabs.addTab(installed_tab, "Installed Applications")
        self.tabs.addTab(available_tab, "Available Applications")
        self.tabs.addTab(suggested_tab, "Suggested Applications")
        self.tabs.addTab(info_tab, "App Info")

        # Set the Installed Applications tab as the default
        self.tabs.setCurrentIndex(0)

        self.tabs.currentChanged.connect(self.on_tab_changed)

        main_layout.addWidget(self.tabs)
        central_widget.setLayout(main_layout)

        # Refresh installed apps on startup
        self.refresh_installed_apps()


    def on_search_text_changed(self, text):
        """
        Trigger search when text length is 2 or more characters.
        """
        if len(text) >= 2:
            self.perform_search()
    
    def on_tab_changed(self, index):
        """
        Slot to handle tab changes.
        """
        # When switching to the Available Applications tab, load apps if not already loaded
        if index == 1:
            if not self.available_apps_loaded:
                self.load_available_apps()
            else:
                self.display_available_apps(self.all_available_apps)
        # When switching to the Suggested Applications tab, load suggested apps
        elif index == 2:
            self.load_suggested_apps()

    def run_script_async(self, args, callback):
        """
        Runs a script asynchronously using QThread and Worker.
        """
        # Disable UI elements
        self.set_ui_enabled(False)

        # Show progress dialog
        self.progress_dialog = QProgressDialog("Processing...", None, 0, 0, self)
        self.progress_dialog.setWindowTitle("Please Wait")
        self.progress_dialog.setWindowModality(Qt.ApplicationModal)
        self.progress_dialog.setCancelButton(None)  # Disable cancel
        self.progress_dialog.show()

        # Setup worker and thread
        thread = QThread()
        worker = Worker(args)
        worker.moveToThread(thread)

        # Keep references
        self.threads.append(thread)
        self.workers.append(worker)

        # Connect signals and slots
        thread.started.connect(worker.run)
        worker.output_ready.connect(callback)
        worker.error_occurred.connect(self.handle_error)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self.on_thread_finished)

        thread.start()

    @Slot()
    def on_thread_finished(self):
        """
        Slot to handle the completion of a worker thread.
        """
        # Close progress dialog
        self.progress_dialog.close()
        # Re-enable UI elements
        self.set_ui_enabled(True)
        # Clean up threads
        sender_thread = self.sender()
        if sender_thread in self.threads:
            self.threads.remove(sender_thread)

    def handle_error(self, error_output):
        """
        Handles errors emitted by the worker.
        """
        QMessageBox.critical(self, "Error", error_output)

    def set_ui_enabled(self, enabled):
        """
        Enable or disable UI elements.
        """
        self.tabs.setEnabled(enabled)
        self.update_button.setEnabled(enabled)
        self.remove_button.setEnabled(enabled)
        self.update_all_button.setEnabled(enabled)
        self.refresh_button.setEnabled(enabled)
        self.install_button.setEnabled(enabled)
        self.search_box.setEnabled(enabled)
        self.search_button.setEnabled(enabled)

    def refresh_installed_apps(self):
        """
        Refreshes the list of installed applications.
        """
        def callback(output):
            self.app_list_view_model = None  # Clear previous model
            apps = []
            if output:
                lines = output.strip().split('\n')
                for app_line in lines:
                    if not app_line.strip():
                        continue
                    is_app_line = app_line.strip().startswith('◆')
                    if not is_app_line:
                        continue  # Skip lines that are not apps
                    app_name = self.extract_app_name(app_line)
                    description = self.extract_app_description(app_line)
                    description = ' '.join(description.splitlines())
                    apps.append((app_name, description, is_app_line))
            self.app_list_view_model = AppListModel(apps)
            self.app_list_view.setModel(self.app_list_view_model)

        self.run_script_async(["-f"], callback)

    def load_available_apps(self):
        """
        Loads the list of available applications from a file asynchronously.
        """
        # Show progress dialog
        self.progress_dialog = QProgressDialog("Loading available applications...", None, 0, 0, self)
        self.progress_dialog.setWindowTitle("Please Wait")
        self.progress_dialog.setWindowModality(Qt.ApplicationModal)
        self.progress_dialog.setCancelButton(None)  # Disable cancel
        self.progress_dialog.show()

        def on_apps_loaded(apps):
            self.progress_dialog.close()
            self.available_apps_loaded = True
            self.all_available_apps = apps
            self.display_available_apps(apps)

        app_path = os.path.join(os.path.expanduser("~/.local/share/AM"), "x86_64-apps") 

        self.load_apps_from_file(app_path, on_apps_loaded)

    def perform_search(self):
        """
        Performs the search operation by filtering the available apps.
        """
        search_text = self.search_box.text().strip()
        if not search_text:
            QMessageBox.warning(self, "Input Required", "Please enter a search term.")
            return
        filtered_apps = [
            (app_name, description, is_app)
            for app_name, description, is_app in self.all_available_apps
            if search_text.lower() in app_name.lower() or search_text.lower() in description.lower()
        ]
        self.display_available_apps(filtered_apps)

    def display_available_apps(self, apps):
        """
        Displays the list of available applications in the UI.
        """
        self.available_apps = apps
        self.available_list_view_model = AppListModel(apps)
        self.available_list_view.setModel(self.available_list_view_model)

    def extract_app_name(self, app_line):
        """
        Extracts the app name from a line of script output.
        """
        if '|' in app_line:
            app_name = app_line.split('|')[0].strip().lstrip('◆').strip()
        elif ' : ' in app_line:
            app_name = app_line.split(' : ', 1)[0].strip().lstrip('◆').strip()
        else:
            app_name = app_line.strip().lstrip('◆').strip()
        return app_name

    def extract_app_description(self, app_line):
        """
        Extracts the app description from a line of script output.
        """
        if ' : ' in app_line:
            description = app_line.split(' : ', 1)[1].strip()
            return description
        elif '|' in app_line:
            description = app_line.split('|', 1)[1].strip()
            return description
        else:
            return ''
            
    def update_appimage(self):
        """
        Updates the selected AppImage.
        """
        index = self.app_list_view.currentIndex()

        if not index.isValid():
            QMessageBox.warning(self, "No Selection", "Please select an application to update.")
            return

        is_app = index.data(Qt.UserRole + 1)
        if not is_app:
            QMessageBox.warning(self, "Invalid Selection", "Please select a valid application to update.")
            return

        app_name = index.data(Qt.UserRole)
        if not app_name:
            QMessageBox.warning(self, "No Application Name", "Application name not found.")
            return

        print(f"Updating app: '{app_name}'")  # Debugging statement

        def callback(output):
            QMessageBox.information(self, "Update Success", output)
            self.refresh_installed_apps()

        self.run_script_async(["-u", app_name], callback)

    def remove_appimage(self):
        """
        Removes the selected AppImage.
        """
        index = self.app_list_view.currentIndex()

        if not index.isValid():
            QMessageBox.warning(self, "No Selection", "Please select an application to remove.")
            return

        is_app = index.data(Qt.UserRole + 1)
        if not is_app:
            QMessageBox.warning(self, "Invalid Selection", "Please select a valid application to remove.")
            return

        app_name = index.data(Qt.UserRole)
        if not app_name:
            QMessageBox.warning(self, "No Application Name", "Application name not found.")
            return

        reply = QMessageBox.question(
            self,
            "Confirm Removal",
            f"Are you sure you want to remove {app_name}?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            def callback(output):
                QMessageBox.information(self, "Removal Success", output)
                self.refresh_installed_apps()

            self.run_script_async(["-R", app_name], callback)
            
    def update_all_apps(self):
        """
        Updates all installed AppImages.
        """
        def callback(output):
            QMessageBox.information(self, "Update All Success", output)
            self.refresh_installed_apps()

        self.run_script_async(["-u"], callback)

   
    def load_apps_from_file(self, file_path, callback):
        """
        Loads apps from a file in a separate thread.
        """
        thread = QThread()
        worker = FileLoaderWorker(file_path)
        worker.moveToThread(thread)

        # Keep references
        self.threads.append(thread)
        self.workers.append(worker)

        worker.apps_loaded.connect(callback)
        worker.apps_loaded.connect(thread.quit)
        worker.apps_loaded.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self.on_thread_finished)
        thread.started.connect(worker.run)
        thread.start()

    def install_selected_appimage(self):
        """
        Installs the selected AppImage from the Available Applications list.
        """
        index = self.available_list_view.currentIndex()

        if not index.isValid():
            QMessageBox.warning(self, "No Selection", "Please select an application to install.")
            return
        app_name = index.data(Qt.UserRole)
        if not app_name:
            return

        print(f"Installing app: '{app_name}'")  # Debugging statement

        def callback(output):
            # Extract the relevant final section after the installation summary
            success_message_start = "The following new programs have been installed:"
            if success_message_start in output:
                display_output = output.split(success_message_start)[-1].strip()
                display_output = f"{success_message_start}\n{display_output}"
            else:
                # If the success message is not found, fallback to displaying the full output
                display_output = output

            # Show the message in a QMessageBox
            QMessageBox.information(self, "Installation Complete", display_output)
            self.refresh_installed_apps()

        # Run the installation script
        self.run_script_async(["-i", app_name], callback)
    
    def install_selected_appimage_from_suggested(self, index):
        """
        Installs the selected AppImage from the Suggested Applications list.
        """
        if not index.isValid():
            QMessageBox.warning(self, "No Selection", "Please select an application to install.")
            return
    
        app_name = index.data(Qt.DisplayRole)  # Use DisplayRole to retrieve the app name
        if not app_name:
            return

        def callback(output):
            # Extract the relevant final section after the installation summary
            success_message_start = "The following new programs have been installed:"
            if success_message_start in output:
                display_output = output.split(success_message_start)[-1].strip()
                display_output = f"{success_message_start}\n{display_output}"
            else:
                # If the success message is not found, fallback to displaying the full output
                display_output = output

            # Show the message in a QMessageBox
            QMessageBox.information(self, "Installation Complete", display_output)
            self.refresh_installed_apps()

        # Run the installation script
        self.run_script_async(["-i", app_name], callback)

    def install_selected_from_button(self):
        """
        Installs the currently selected app from the Suggested Applications list.
        """
        index = self.suggested_list_view.currentIndex()
        if not index.isValid():
            QMessageBox.warning(self, "No Selection", "Please select an application to install.")
            return
    
        self.install_selected_appimage_from_suggested(index)


    def load_suggested_apps(self):
        """
        Loads suggested applications from a JSON URL or uses a default sample.
        """
        # Show progress dialog
        self.progress_dialog = QProgressDialog("Loading suggested applications...", None, 0, 0, self)
        self.progress_dialog.setWindowTitle("Please Wait")
        self.progress_dialog.setWindowModality(Qt.ApplicationModal)
        self.progress_dialog.setCancelButton(None)  # Disable cancel
        self.progress_dialog.show()

        def on_suggested_apps_loaded(apps):
            self.progress_dialog.close()
            self.suggested_apps_model = SuggestedAppModel(apps)
            self.suggested_list_view.setModel(self.suggested_apps_model)

        # Load suggested apps in a separate thread
        thread = QThread()
        worker = SuggestedAppsLoader()
        worker.moveToThread(thread)

        # Keep references
        self.threads.append(thread)
        self.workers.append(worker)

        worker.apps_loaded.connect(on_suggested_apps_loaded)
        worker.apps_loaded.connect(thread.quit)
        worker.apps_loaded.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self.on_thread_finished)
        thread.started.connect(worker.run)
        thread.start()

    def show_installed_context_menu(self, position):
        """
        Shows context menu for installed applications if the selected item is an app.
        """
        index = self.app_list_view.indexAt(position)
        if not index.isValid() or not index.data(Qt.UserRole + 1):  # Only allow app items
            return
    
        app_name = index.data(Qt.UserRole)
        if not app_name:
            return

        menu = QMenu()
        update_action = menu.addAction(qta.icon('fa.refresh'), "Update")
        remove_action = menu.addAction(qta.icon('fa.trash'), "Remove")
        info_action = menu.addAction("About")

        action = menu.exec_(self.app_list_view.viewport().mapToGlobal(position))
        if action == update_action:
            self.app_list_view.setCurrentIndex(index)
            self.update_appimage()
        elif action == remove_action:
            self.app_list_view.setCurrentIndex(index)
            self.remove_appimage()
        elif action == info_action:
            self.show_app_info(app_name)


    def show_available_context_menu(self, position):
        """
        Shows context menu for available applications.
        """
        index = self.available_list_view.indexAt(position)
        if not index.isValid():
            return
        app_name = index.data(Qt.UserRole)
        if not app_name:
            return

        menu = QMenu()
        install_action = menu.addAction(qta.icon('fa.download'), "Install")
        info_action = menu.addAction("About")

        action = menu.exec_(self.available_list_view.viewport().mapToGlobal(position))
        if action == install_action:
            self.available_list_view.setCurrentIndex(index)
            self.install_selected_appimage()
        elif action == info_action:
            self.show_app_info(app_name)

    def show_suggested_context_menu(self, position):
        """
        Shows context menu for suggested applications.
        """
        index = self.suggested_list_view.indexAt(position)
        if not index.isValid():
            return
        app_name = index.data(Qt.DisplayRole)  # Ensure correct role is used for app name retrieval
        if not app_name:
            return

        menu = QMenu()
        install_action = menu.addAction(qta.icon('fa.download'), "Install")
        info_action = menu.addAction("About")

        action = menu.exec_(self.suggested_list_view.viewport().mapToGlobal(position))
        if action == install_action:
            self.install_selected_appimage_from_suggested(index)  # Pass index here
        elif action == info_action:
            self.show_app_info(app_name)


    def show_app_info(self, app_name):
        """
        Displays information about the selected application.
        """
        def callback(output):
            output = strip_ansi_escape_codes(output)
            output = make_links_clickable(output)
            msg_box = QMessageBox()
            msg_box.setWindowTitle(f"About - {app_name}")
            msg_box.setTextFormat(Qt.RichText)
            msg_box.setText(f"<p>{output}</p>")
            msg_box.setStandardButtons(QMessageBox.Ok)
            msg_box.exec_()

        self.run_script_async(["about", app_name], callback)


class SuggestedAppsLoader(QObject):
    """
    Worker class to load suggested apps in a separate thread.
    """
    apps_loaded = Signal(list)

    def run(self):
        suggested_apps_data = []
        try:
            # Replace with your JSON URL
            json_url = "https://marko-app.netlify.app/tai.json"
            response = requests.get(json_url)
            if response.status_code == 200:
                data = response.json()
                suggested_apps_data = data.get("suggested_apps", [])  # Extract the app list
                print(f"Fetched suggested apps: {suggested_apps_data}")  # Debugging log
            else:
                raise Exception(f"Failed to fetch JSON, status code: {response.status_code}")
        except Exception as e:
            print(f"Error fetching suggested apps: {e}")
            # Use default sample if JSON not found
            suggested_apps_data = [
                {
                    "app_name": "abiword",
                    "description": "AbiWord is a free word processing program.",
                    "icon_url": "https://icons.iconarchive.com/icons/papirus-team/papirus-apps/512/abiword-icon.png"
                }
            ]
            print("Using default suggested apps data.")
        self.apps_loaded.emit(suggested_apps_data)

def is_installed(package_name):
    """Check if a package is installed using dpkg -s."""
    try:
        result = subprocess.run(
            ["dpkg", "-s", package_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        return result.returncode == 0
    except Exception as e:
        print(f"Error checking package {package_name}: {e}")
        return False

def install_dependencies():
    """Install required dependencies if missing."""
    dependencies = [
        "coreutils", "curl", "grep", "less", "sed", "wget",
        "sudo", "binutils", "unzip", "tar", "torsocks", "zsync"
    ]
    missing_deps = [dep for dep in dependencies if not is_installed(dep)]

    if missing_deps:
        try:
            subprocess.run(["pkexec", "apt", "install", "-y"] + missing_deps, check=True)
            print(f"Installed missing dependencies: {', '.join(missing_deps)}")
        except subprocess.CalledProcessError as e:
            print(f"Failed to install dependencies: {e}")
            QMessageBox.critical(None, "Installation Failed", f"Dependencies could not be installed: {', '.join(missing_deps)}")
            return False
    else:
        print("All required dependencies are installed.")
    return True

def download_and_run_am_installer():
    """Download and run the AM-INSTALLER script with automated input."""
    try:
        # Download the installer script
        subprocess.run(
            ["wget", "-q", "https://raw.githubusercontent.com/ivan-hc/AM/main/AM-INSTALLER", "-O", "./AM-INSTALLER"],
            check=True
        )
        subprocess.run(["chmod", "+x", "./AM-INSTALLER"], check=True)

        # Run the installer script with predefined options
        process = subprocess.Popen(
            ["./AM-INSTALLER"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        # Provide input to select option 2 for local installation
        process.communicate(input="2\n")

        if process.returncode != 0:
            print(f"AM-INSTALLER returned error code {process.returncode}")
            print(f"Installer stderr: {process.stderr.read()}")
            QMessageBox.critical(None, "Installation Error", "AppMan installation failed during installer execution.")
            return False

        print("AppMan installed successfully.")
    except Exception as e:
        print(f"Failed to run AM-INSTALLER: {e}")
        QMessageBox.critical(None, "Installation Error", f"An error occurred during AppMan installation: {e}")
        return False
    finally:
        # Clean up installer file
        if os.path.exists("./AM-INSTALLER"):
            os.remove("./AM-INSTALLER")
    return True

def configure_appman_directory(install_dir):
    """Run 'appman' to set the installation directory."""
    try:
        appman_path = os.path.expanduser("~/.local/bin/appman")
        process = subprocess.Popen(
            [appman_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        # Provide the installation directory when prompted
        process.communicate(input=f"{install_dir}\n")

        if process.returncode != 0:
            print(f"AppMan configuration returned error code {process.returncode}")
            print(f"Configuration stderr: {process.stderr.read()}")
            QMessageBox.critical(None, "Configuration Error", "AppMan configuration failed.")
            return False

        print("AppMan configured successfully.")
    except Exception as e:
        print(f"Failed to configure AppMan: {e}")
        QMessageBox.critical(None, "Configuration Error", f"An error occurred during AppMan configuration: {e}")
        return False
    return True

def install_appman_if_missing():
    """Check if 'appman' is installed; if not, install and configure it."""
    appman_path = os.path.expanduser("~/.local/bin/appman")
    install_dir = os.path.expanduser("~/.tai")

    # Check if appman is already installed
    if os.path.exists(appman_path):
        print("'appman' command found.")
        return True

    print("'appman' command not found. Installing...")

    # Install dependencies
    if not install_dependencies():
        print("Failed to install dependencies.")
        return False

    # Download and run the installer
    if not download_and_run_am_installer():
        print("Failed to install AppMan.")
        return False

    # Verify installation
    if not os.path.exists(appman_path):
        QMessageBox.critical(None, "Installation Error", "AppMan installation failed or was incomplete.")
        return False

    # Configure appman to use the desired installation directory
    if not configure_appman_directory(install_dir):
        print("Failed to configure AppMan.")
        return False

    print("AppMan is ready to use.")
    return True

def main():
    # Initialize the QApplication
    app = QApplication(sys.argv)

    # Check if AppMan is installed and configured; install if missing
    if not install_appman_if_missing():
        print("AppMan installation or configuration failed. Exiting.")
        sys.exit(1)  # Exit if installation or configuration was unsuccessful

    # Set app name, version, and icon
    app_name = "Tai"
    app_version = "1.0.0"
    app.setApplicationName(app_name)
    app.setApplicationVersion(app_version)

    # Set the application icon
    try:
        if getattr(sys, 'frozen', False):  # If running in a bundled environment like PyInstaller
            icon_path = os.path.join(sys._MEIPASS, "tai_appimage.png")
        else:
            icon_path = "tai_appimage.png"
        app.setWindowIcon(QIcon(icon_path))
    except Exception as e:
        print(f"Error loading icon: {e}")

    # Apply Material Design theme
    
    apply_stylesheet(app, theme='dark_cyan.xml')
    app.setStyleSheet(app.styleSheet() + " QListView { font-size: 14pt; }")

    # Initialize and display the main window
    window = AppImageManagerGUI()
    window.setWindowTitle(f"{app_name} - AppImage Manager v.{app_version}")
    window.show()

    sys.exit(app.exec())

if __name__ == "__main__":
    main()
