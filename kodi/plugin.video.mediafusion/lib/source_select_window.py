import xbmcaddon
import xbmcgui


class SourceSelectWindow(xbmcgui.WindowXMLDialog):
    """Custom source selection window for MediaFusion streams."""

    def __init__(self, *args, **kwargs):
        self.streams = kwargs.pop("streams", []) or []
        self.filtered_streams = list(self.streams)
        self.window_title = kwargs.pop("window_title", "Select Stream")
        self.has_more = bool(kwargs.pop("has_more", False))
        self.on_load_more = kwargs.pop("on_load_more", None)
        self.selected_stream = None
        self.sort_modes = [
            ("smart", "SMART", "C>R>S>Z", "cache, resolution, seeders, size"),
            ("quality", "QUALITY", "C>R>S", "cache, resolution, seeders"),
            ("seeders", "SEEDERS", "C>S>R", "cache, seeders, resolution"),
            ("size", "SIZE", "C>Z>R", "cache, size, resolution"),
            ("provider", "PROVIDER", "C>P>R", "cache, provider, resolution"),
        ]
        self.filter_modes = [
            ("all", "ALL"),
            ("cached", "CACHED"),
            ("torrent", "TORRENT"),
            ("usenet", "USENET"),
            ("direct", "DIRECT"),
        ]
        self.sort_mode_index = 0
        self.filter_mode_index = 0
        super().__init__(*args, **kwargs)

    def onInit(self):
        self.list_control = self.getControl(1100)
        self.title_control = self.getControl(1200)
        self.status_control = self.getControl(1203)
        self.detail_control = self.getControl(1201)
        self.badges_control = self.getControl(1204)
        self.plot_control = self.getControl(1202)
        self.sort_control = self.getControl(1301)
        self.filter_control = self.getControl(1302)

        self.title_control.setLabel(self.window_title)
        self._refresh_stream_list(reset_selection=True)

        self.setFocusId(1100)

    def onClick(self, control_id):
        if control_id == 1100:
            self._select_current_stream()
        elif control_id == 1301:
            self._cycle_sort_mode()
        elif control_id == 1302:
            self._cycle_filter_mode()
        elif control_id in (1101, 1102):
            self.close()

    def onAction(self, action):
        action_id = action.getId()
        if action_id in (xbmcgui.ACTION_NAV_BACK, xbmcgui.ACTION_PREVIOUS_MENU):
            self.close()
            return

        if action_id == xbmcgui.ACTION_SELECT_ITEM and self.getFocusId() == 1100:
            self._select_current_stream()
            return

        if action_id == 117:  # context menu action
            self._open_quick_menu()
            return

        self._update_details()

    def _select_current_stream(self):
        position = self.list_control.getSelectedPosition()
        if self.has_more and position == len(self.filtered_streams):
            self._load_more_streams()
            return
        if 0 <= position < len(self.filtered_streams):
            self.selected_stream = self.filtered_streams[position]
        self.close()

    def _load_more_streams(self):
        if not self.has_more or not callable(self.on_load_more):
            return

        self.status_control.setLabel("Loading more streams...")
        new_streams, has_more = self.on_load_more()
        self.has_more = bool(has_more)

        if new_streams:
            self.streams.extend(new_streams)
            xbmcgui.Dialog().notification(
                "MediaFusion",
                f"Loaded {len(new_streams)} additional streams",
                xbmcgui.NOTIFICATION_INFO,
            )
        elif not self.has_more:
            xbmcgui.Dialog().notification(
                "MediaFusion",
                "No more streams available",
                xbmcgui.NOTIFICATION_INFO,
            )

        self._refresh_stream_list(reset_selection=False)

    def _cycle_sort_mode(self):
        self.sort_mode_index = (self.sort_mode_index + 1) % len(self.sort_modes)
        sort_name = self.sort_modes[self.sort_mode_index][1]
        sort_desc = self.sort_modes[self.sort_mode_index][3]
        xbmcgui.Dialog().notification("MediaFusion", f"Sort {sort_name}: {sort_desc}", xbmcgui.NOTIFICATION_INFO)
        self._refresh_stream_list(reset_selection=True)

    def _cycle_filter_mode(self):
        self.filter_mode_index = (self.filter_mode_index + 1) % len(self.filter_modes)
        self._refresh_stream_list(reset_selection=True)

    def _open_quick_menu(self):
        options = [
            f"Sort: {self.sort_modes[self.sort_mode_index][1]}",
            f"Filter: {self.filter_modes[self.filter_mode_index][1]}",
            "Apply next sort mode",
            "Apply next filter mode",
        ]
        selected = xbmcgui.Dialog().contextmenu(options)
        if selected == 2:
            self._cycle_sort_mode()
        elif selected == 3:
            self._cycle_filter_mode()

    def _apply_filter(self, stream_items):
        filter_key = self.filter_modes[self.filter_mode_index][0]
        if filter_key == "all":
            return list(stream_items)
        if filter_key == "cached":
            return [item for item in stream_items if item.get("sort_cached", 1) == 0]
        if filter_key == "torrent":
            return [item for item in stream_items if item.get("stream_type_raw") == "TORRENT"]
        if filter_key == "usenet":
            return [item for item in stream_items if item.get("stream_type_raw") == "USENET"]
        if filter_key == "direct":
            return [
                item
                for item in stream_items
                if item.get("stream_type_raw") in {"HTTP", "TELEGRAM", "YOUTUBE", "ACESTREAM"}
            ]
        return list(stream_items)

    def _apply_sort(self, stream_items):
        sort_key = self.sort_modes[self.sort_mode_index][0]
        if sort_key == "quality":
            return sorted(
                stream_items,
                key=lambda item: (
                    item.get("sort_cached", 1),
                    -item.get("sort_resolution", 0),
                    -item.get("sort_seeders", 0),
                    item.get("list_primary", ""),
                ),
            )
        if sort_key == "seeders":
            return sorted(
                stream_items,
                key=lambda item: (
                    item.get("sort_cached", 1),
                    -item.get("sort_seeders", 0),
                    -item.get("sort_resolution", 0),
                    item.get("list_primary", ""),
                ),
            )
        if sort_key == "size":
            return sorted(
                stream_items,
                key=lambda item: (
                    item.get("sort_cached", 1),
                    -(item.get("sort_size", 0) or 0),
                    -item.get("sort_resolution", 0),
                    item.get("list_primary", ""),
                ),
            )
        if sort_key == "provider":
            return sorted(
                stream_items,
                key=lambda item: (
                    item.get("sort_cached", 1),
                    item.get("video_info", {}).get("provider", ""),
                    -item.get("sort_resolution", 0),
                    item.get("list_primary", ""),
                ),
            )
        # smart sort
        return sorted(
            stream_items,
            key=lambda item: (
                item.get("sort_cached", 1),
                -item.get("sort_resolution", 0),
                -item.get("sort_seeders", 0),
                -(item.get("sort_size", 0) or 0),
                item.get("list_primary", ""),
            ),
        )

    def _refresh_stream_list(self, reset_selection=False):
        previous_selection = self.list_control.getSelectedPosition() if not reset_selection else 0
        filtered = self._apply_filter(self.streams)
        self.filtered_streams = self._apply_sort(filtered)

        self.list_control.reset()
        for stream_item in self.filtered_streams:
            provider = stream_item.get("video_info", {}).get("provider", "")
            stream_type = stream_item.get("video_info", {}).get("stream_type", "")
            cache = "CACHED" if stream_item.get("sort_cached", 1) == 0 else "UNCACHED"
            list_item = xbmcgui.ListItem(
                label=stream_item.get("list_primary", stream_item.get("main_label", "Stream")),
                label2=stream_item.get("list_secondary", stream_item.get("detail_label", "")),
            )
            list_item.setProperty("mf.primary", stream_item.get("list_primary", ""))
            list_item.setProperty("mf.secondary", stream_item.get("list_secondary", ""))
            list_item.setProperty("mf.provider", provider)
            list_item.setProperty("mf.logo", (provider or "MF")[:3].upper())
            list_item.setProperty("mf.type", stream_type)
            list_item.setProperty("mf.cache", cache)
            self.list_control.addItem(list_item)

        if self.has_more:
            load_more_item = xbmcgui.ListItem(
                label="[B]Load More Streams[/B]",
                label2=f"Loaded {len(self.streams)} streams - click to fetch next page",
            )
            load_more_item.setProperty("mf.logo", "+")
            load_more_item.setProperty("mf.type", "MORE")
            load_more_item.setProperty("mf.cache", "")
            self.list_control.addItem(load_more_item)

        self.sort_control.setLabel(f"Sort: {self.sort_modes[self.sort_mode_index][1]}")
        self.filter_control.setLabel(f"Filter: {self.filter_modes[self.filter_mode_index][1]}")

        total_count = len(self.streams)
        visible_count = len(self.filtered_streams)
        cached_count = sum(1 for stream_item in self.filtered_streams if stream_item.get("sort_cached", 1) == 0)
        sort_desc = self.sort_modes[self.sort_mode_index][2]
        self.status_control.setLabel(
            f"Showing {visible_count}/{total_count} loaded  |  Cached: {cached_count}  |  "
            f"Sort: {self.sort_modes[self.sort_mode_index][1]} ({sort_desc})"
            f"{'  |  More: YES' if self.has_more else ''}"
        )

        total_items = len(self.filtered_streams) + (1 if self.has_more else 0)
        if total_items > 0:
            self.list_control.selectItem(min(previous_selection, total_items - 1))
        self._update_details()

    def _update_details(self):
        if not self.filtered_streams:
            self.detail_control.setLabel("No streams available")
            self.badges_control.setLabel("")
            if self.has_more:
                self.plot_control.setText("Change filter or select 'Load More Streams'.")
            else:
                self.plot_control.setText("")
            return

        position = self.list_control.getSelectedPosition()
        if self.has_more and position == len(self.filtered_streams):
            self.detail_control.setLabel("Load More Streams")
            self.badges_control.setLabel("[COLOR FF90CAF9][B]PAGINATION[/B][/COLOR]")
            self.plot_control.setText(
                "Fetch the next page of results from the server.\nCurrent filters and sorting will still apply."
            )
            return

        if position < 0 or position >= len(self.filtered_streams):
            return

        selected = self.filtered_streams[position]
        detail_title = selected.get("detail_title", selected.get("detail_label", ""))
        detail_badges = selected.get("detail_badges", "")
        plot = selected.get("plot", "")

        self.detail_control.setLabel(detail_title)
        self.badges_control.setLabel(detail_badges)
        self.plot_control.setText(plot)


def open_source_select_window(streams, has_more=False, on_load_more=None):
    addon = xbmcaddon.Addon("plugin.video.mediafusion")
    addon_path = addon.getAddonInfo("path")
    window = SourceSelectWindow(
        "source_select_window.xml",
        addon_path,
        "default",
        "1080i",
        streams=streams,
        has_more=has_more,
        on_load_more=on_load_more,
        window_title="MediaFusion Source Select",
    )
    window.doModal()
    selected_stream = window.selected_stream
    del window
    return selected_stream
