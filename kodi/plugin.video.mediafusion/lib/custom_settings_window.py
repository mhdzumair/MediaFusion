import subprocess
import time
from urllib.parse import quote, urljoin

import requests
import xbmc
import xbmcaddon
import xbmcgui


def _extract_wrapped_api_error(payload):
    """Return (detail, status_code) for wrapped API errors, else (None, None)."""
    if not isinstance(payload, dict) or payload.get("error") is not True:
        return None, None
    detail = payload.get("detail")
    status_code = payload.get("status_code")
    return (
        detail if isinstance(detail, str) else "Request failed",
        status_code if isinstance(status_code, int) else None,
    )


def _map_kodi_error_message(detail, status_code):
    normalized_detail = detail.lower()

    if status_code == 429 or "rate limit" in normalized_detail:
        return "Too many requests. Please wait a few seconds and try again."

    if "invalid setup code" in normalized_detail:
        return "Invalid or expired setup code. Generate a new code and try again."

    if status_code == 401 or "api key" in normalized_detail or "api password" in normalized_detail:
        return "Authentication failed. Verify API key/password for this MediaFusion instance."

    if "validation error" in normalized_detail:
        return "Invalid setup code format. Use the 6-character code shown in Kodi."

    return detail


class CustomSettingsWindow(xbmcgui.WindowXMLDialog):
    def __init__(self, *args, **kwargs):
        super(CustomSettingsWindow, self).__init__(*args, **kwargs)
        self.addon = xbmcaddon.Addon("plugin.video.mediafusion")
        self.base_url = self.addon.getSetting("base_url")
        self.secret_string = self.addon.getSetting("secret_string")
        self.api_password = self.addon.getSetting("api_password").strip()
        self.is_running = True
        self.poll_interval = 5  # Poll every 5 seconds
        self.configure_url = ""

    def onInit(self):
        self.base_url_label = self.getControl(310)
        self.base_url_control = self.getControl(301)
        self.secret_string_label = self.getControl(311)
        self.secret_string_control = self.getControl(302)
        self.api_password_label = self.getControl(312)
        self.api_password_control = self.getControl(313)
        self.qr_code_image = self.getControl(303)
        self.instruction_label = self.getControl(304)
        self.configure_button = self.getControl(305)
        self.open_config_button = self.getControl(306)
        self.back_button = self.getControl(307)
        self.setup_code_label = self.getControl(308)
        self.time_remaining_label = self.getControl(309)

        self.update_url_display()
        self.update_secret_display()
        self.update_api_password_display()
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
        elif controlId == 313:  # API Password
            self.edit_api_password()
        elif controlId == 305:  # Configure button
            self.configure_secret()
        elif controlId == 306:  # Open Configuration Page button
            self.open_configuration_page()
        elif controlId == 307:  # Back button
            self.is_running = False
            self.close()

    def edit_base_url(self):
        new_base_url = xbmcgui.Dialog().input("Enter MediaFusion Base URL", self.base_url)
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

    def edit_api_password(self):
        new_api_password = xbmcgui.Dialog().input(
            "Enter API Password (Optional)",
            self.api_password,
            option=xbmcgui.ALPHANUM_HIDE_INPUT,
        )
        if new_api_password is not None:
            self.api_password = new_api_password.strip()
            self.addon.setSetting("api_password", self.api_password)
            self.update_api_password_display()

    def update_url_display(self):
        if self.base_url:
            display_url = self.base_url[:30] + "..." if len(self.base_url) > 33 else self.base_url
            self.base_url_control.setLabel(display_url)
        else:
            self.base_url_control.setLabel("Click to set Base URL")

    def update_secret_display(self):
        if self.secret_string:
            self.secret_string_control.setLabel("*" * len(self.secret_string))
        else:
            self.secret_string_control.setLabel("Not set")

    def update_api_password_display(self):
        if self.api_password:
            self.api_password_control.setLabel("*" * len(self.api_password))
        else:
            self.api_password_control.setLabel("Not set (optional)")

    def _get_api_headers(self):
        headers = {}
        if self.api_password:
            headers["X-API-Key"] = self.api_password.strip()
        return headers

    def _append_headers_to_url(self, url):
        """Append Kodi-style HTTP headers to URL: url|Header=Value&Header2=Value2."""
        headers = self._get_api_headers()
        if not headers:
            return url
        header_query = "&".join(f"{key}={quote(value, safe='')}" for key, value in headers.items())
        return f"{url}|{header_query}"

    def update_instructions(self):
        instructions = (
            "1. Set the MediaFusion Base URL\n"
            "2. Optionally set API password (private instances)\n"
            "3. Click 'Configure Secret' to generate a setup code\n"
            "4. Scan the QR code, open the configuration page, or enter the setup code on your device\n"
            "5. Complete the setup process on your device"
        )
        self.instruction_label.setText(instructions)

    def configure_secret(self):
        try:
            data = self.secret_string if self.secret_string else ""
            response = requests.post(
                urljoin(self.base_url, "api/v1/kodi/generate-setup-code"),
                json=data,
                headers=self._get_api_headers(),
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
            error_detail, error_status_code = _extract_wrapped_api_error(data)
            if error_detail:
                raise RuntimeError(_map_kodi_error_message(error_detail, error_status_code))

            code = data["code"]
            qr_code_url = data["qr_code_url"]
            self.configure_url = data["configure_url"]
            expires_in = data["expires_in"]

            self.qr_code_image.setImage(self._append_headers_to_url(qr_code_url))
            start_time = time.time()
            last_poll_time = 0

            self.open_config_button.setVisible(True)

            while time.time() - start_time < expires_in and self.is_running:
                current_time = time.time()
                remaining_time = int(expires_in - (current_time - start_time))
                minutes, seconds = divmod(remaining_time, 60)

                self.setup_code_label.setLabel(f"Setup Code: {code}")
                self.time_remaining_label.setLabel(f"Time remaining: {minutes:02d}:{seconds:02d}")

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
            status_code = getattr(e.response, "status_code", None)
            error_message = (
                "Too many requests. Please wait a few seconds and try again."
                if status_code == 429
                else "Authentication failed. Verify API key/password for this MediaFusion instance."
                if status_code == 401
                else f"Error: {str(e)}"
            )
            self.setup_code_label.setLabel(error_message)
            self.time_remaining_label.setLabel("")
            self.open_config_button.setVisible(False)
            self.qr_code_image.setImage("")
        except Exception as e:
            self.setup_code_label.setLabel(f"Error: {str(e)}")
            self.time_remaining_label.setLabel("")
            self.open_config_button.setVisible(False)
            self.qr_code_image.setImage("")

    def poll_for_secret(self, code):
        try:
            response = requests.get(
                urljoin(self.base_url, f"api/v1/kodi/get-manifest/{code}"),
                headers=self._get_api_headers(),
                timeout=10,
            )
            if response.status_code == 200:
                data = response.json()
                error_detail, error_status_code = _extract_wrapped_api_error(data)
                if error_detail:
                    if error_status_code == 404 and error_detail == "Manifest URL not found":
                        # Expected while waiting for user to submit code in web UI.
                        return False
                    if error_status_code == 429:
                        self.poll_interval *= 2  # Double the polling interval on rate limit
                        xbmc.log(
                            f"Rate limit hit. Increasing poll interval to {self.poll_interval} seconds.",
                            xbmc.LOGWARNING,
                        )
                        return False
                    raise RuntimeError(_map_kodi_error_message(error_detail, error_status_code))

                new_secret_string = data.get("secret_string")
                if not new_secret_string:
                    return False

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
        except RuntimeError as e:
            xbmc.log(f"Kodi setup failed: {str(e)}", xbmc.LOGERROR)
            self.setup_code_label.setLabel(str(e))
            self.time_remaining_label.setLabel("")
            self.open_config_button.setVisible(False)
            self.qr_code_image.setImage("")
            self.is_running = False
        except ValueError as e:
            xbmc.log(f"Error parsing setup response: {str(e)}", xbmc.LOGERROR)
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
    window = CustomSettingsWindow("custom_settings_window.xml", addon_path, "default", "1080i")
    window.doModal()
    del window


if __name__ == "__main__":
    open_settings()
