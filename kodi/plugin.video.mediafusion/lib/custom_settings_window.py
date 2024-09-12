import subprocess
import time
from urllib.parse import urljoin

import requests
import xbmc
import xbmcaddon
import xbmcgui


class CustomSettingsWindow(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        super(CustomSettingsWindow, self).__init__(*args, **kwargs)
        self.addon = xbmcaddon.Addon("plugin.video.mediafusion")
        self.base_url = self.addon.getSetting("base_url")
        self.secret_string = self.addon.getSetting("secret_string")
        self.is_running = True
        self.poll_interval = 5  # Poll every 5 seconds
        self.configure_url = ""

    def onInit(self):
        self.base_url_label = self.getControl(310)
        self.base_url_control = self.getControl(301)
        self.secret_string_label = self.getControl(311)
        self.secret_string_control = self.getControl(302)
        self.qr_code_image = self.getControl(303)
        self.instruction_label = self.getControl(304)
        self.configure_button = self.getControl(305)
        self.open_config_button = self.getControl(306)
        self.back_button = self.getControl(307)
        self.setup_code_label = self.getControl(308)
        self.time_remaining_label = self.getControl(309)

        self.update_url_display()
        self.update_secret_display()
        self.update_instructions()
        self.open_config_button.setVisible(False)

    def onAction(self, action):
        if action.getId() in (xbmcgui.ACTION_NAV_BACK, xbmcgui.ACTION_PREVIOUS_MENU):
            self.is_running = False
            self.close()

    def onClick(self, controlId):
        if controlId == 301:  # Base URL
            self.edit_base_url()
        elif controlId == 302:  # Secret String
            self.edit_secret_string()
        elif controlId == 305:  # Configure button
            self.configure_secret()
        elif controlId == 306:  # Open Configuration Page button
            self.open_configuration_page()
        elif controlId == 307:  # Back button
            self.is_running = False
            self.close()

    def edit_base_url(self):
        new_base_url = xbmcgui.Dialog().input(
            "Enter MediaFusion Base URL", self.base_url
        )
        if new_base_url:
            self.base_url = new_base_url
            self.addon.setSetting("base_url", self.base_url)
            self.update_url_display()

    def edit_secret_string(self):
        new_secret_string = xbmcgui.Dialog().input(
            "Enter Secret String",
            self.secret_string,
            option=xbmcgui.ALPHANUM_HIDE_INPUT,
        )
        if new_secret_string:
            self.secret_string = new_secret_string
            self.addon.setSetting("secret_string", self.secret_string)
            self.update_secret_display()

    def update_url_display(self):
        if self.base_url:
            display_url = (
                self.base_url[:30] + "..." if len(self.base_url) > 33 else self.base_url
            )
            self.base_url_control.setLabel(display_url)
        else:
            self.base_url_control.setLabel("Click to set Base URL")

    def update_secret_display(self):
        if self.secret_string:
            self.secret_string_control.setLabel("*" * len(self.secret_string))
        else:
            self.secret_string_control.setLabel("Not set")

    def update_instructions(self):
        instructions = (
            "1. Set the MediaFusion Base URL\n"
            "2. Click 'Configure Secret' to generate a setup code\n"
            "3. Scan the QR code or Open the Configuration page or use the setup code on your device\n"
            "4. Complete the setup process on your device"
        )
        self.instruction_label.setText(instructions)

    def configure_secret(self):
        try:
            data = self.secret_string if self.secret_string else ""
            response = requests.post(
                urljoin(self.base_url, "kodi/generate_setup_code"), json=data
            )
            data = response.json()
            code = data["code"]
            qr_code_url = data["qr_code_url"]
            self.configure_url = data["configure_url"]
            expires_in = data["expires_in"]

            self.qr_code_image.setImage(qr_code_url)
            start_time = time.time()
            last_poll_time = 0

            self.open_config_button.setVisible(True)

            while time.time() - start_time < expires_in and self.is_running:
                current_time = time.time()
                remaining_time = int(expires_in - (current_time - start_time))
                minutes, seconds = divmod(remaining_time, 60)

                self.setup_code_label.setLabel(f"Setup Code: {code}")
                self.time_remaining_label.setLabel(
                    f"Time remaining: {minutes:02d}:{seconds:02d}"
                )

                if current_time - last_poll_time >= self.poll_interval:
                    if self.poll_for_secret(code):
                        self.setup_code_label.setLabel("Setup completed successfully!")
                        self.time_remaining_label.setLabel("")
                        self.open_config_button.setVisible(False)
                        self.qr_code_image.setImage("")
                        return
                    last_poll_time = current_time

                xbmc.sleep(1000)  # Update every second

            if self.is_running:
                self.setup_code_label.setLabel("Setup failed. Please try again.")
                self.time_remaining_label.setLabel("")
                self.open_config_button.setVisible(False)
                self.qr_code_image.setImage("")

        except requests.exceptions.RequestException as e:
            error_message = (
                "Rate limit exceeded. Please wait and try again."
                if getattr(e.response, "status_code", None) == 429
                else f"Error: {str(e)}"
            )
            self.setup_code_label.setLabel(error_message)
            self.time_remaining_label.setLabel("")
            self.open_config_button.setVisible(False)
            self.qr_code_image.setImage("")

    def poll_for_secret(self, code):
        try:
            response = requests.get(urljoin(self.base_url, f"kodi/get_manifest/{code}"))
            if response.status_code == 200:
                data = response.json()
                new_secret_string = data["secret_string"]
                self.secret_string = new_secret_string
                self.addon.setSetting("secret_string", self.secret_string)
                self.update_secret_display()
                return True
        except requests.exceptions.RequestException as e:
            if getattr(e.response, "status_code", None) == 429:
                self.poll_interval *= 2  # Double the polling interval on rate limit
                xbmc.log(
                    f"Rate limit hit. Increasing poll interval to {self.poll_interval} seconds.",
                    xbmc.LOGWARNING,
                )
            else:
                xbmc.log(f"Error polling for secret: {str(e)}", xbmc.LOGERROR)
        return False

    def open_configuration_page(self):
        if self.configure_url:
            os_win = xbmc.getCondVisibility("system.platform.windows")
            os_osx = xbmc.getCondVisibility("system.platform.osx")
            os_linux = xbmc.getCondVisibility("system.platform.linux")
            os_android = xbmc.getCondVisibility("System.Platform.Android")
            try:
                if os_osx:
                    subprocess.run(["open", self.configure_url], check=True)
                elif os_win:
                    subprocess.run(["start", self.configure_url], check=True)
                elif os_linux and not os_android:
                    subprocess.run(["xdg-open", self.configure_url], check=True)
                elif os_android:
                    xbmc.executebuiltin(
                        f'StartAndroidActivity("","android.intent.action.VIEW","","{self.configure_url}")'
                    )
                else:
                    xbmc.log("Unsupported operating system", xbmc.LOGERROR)
            except Exception as e:
                xbmc.log(f"Error opening configuration page: {str(e)}", xbmc.LOGERROR)


def open_settings():
    addon = xbmcaddon.Addon("plugin.video.mediafusion")
    addon_path = addon.getAddonInfo("path")
    window = CustomSettingsWindow(
        "custom_settings_window.xml", addon_path, "default", "1080i"
    )
    window.doModal()
    del window


if __name__ == "__main__":
    open_settings()
