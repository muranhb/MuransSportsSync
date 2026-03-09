import os
import xml.etree.ElementTree as ET
import traceback
from datetime import datetime, timezone
from tcxreader.tcxreader import TCXReader
from geopy.distance import geodesic
from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.event_message import EventMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.messages.activity_message import ActivityMessage
from fit_tool.profile.messages.device_info_message import DeviceInfoMessage
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.profile_type import (
    FileType, TimerTrigger, Event, EventType, Sport, SubSport, SessionTrigger, Activity
)


class TCX2FITConverter:
    def __init__(self, tcx_path, fit_path, track_type="Run"):
        self.tcx_path = tcx_path
        self.fit_path = fit_path
        self.track_type = track_type

    def convert(self):
        try:
            print(f"  [Debug] 开始处理 TCX 文件: {self.tcx_path}")

            # --- 1. 纯手动解析基础数据 (完全绕开 tcxreader 在无轨迹时的崩溃问题) ---
            it = ET.iterparse(self.tcx_path)
            for _, el in it:
                if '}' in el.tag:
                    el.tag = el.tag.split('}', 1)[1]
            root = it.root

            sport_node = root.find('.//Activity')
            tcx_sport = sport_node.attrib.get('Sport', '') if sport_node is not None else ''

            lap_node = root.find('.//Lap')
            if lap_node is None:
                print(f"  [Debug] 错误: TCX 中没有 Lap 节点")
                return False

            start_time_str = lap_node.attrib.get('StartTime')
            if not start_time_str:
                id_node = root.find('.//Id')
                start_time_str = id_node.text if id_node is not None else "1970-01-01T00:00:00Z"

            dt_obj = datetime.strptime(start_time_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            start_time_ms = int(dt_obj.timestamp() * 1000)

            tt_node = root.find('.//TotalTimeSeconds')
            total_time_sec_computed = float(tt_node.text) if (tt_node is not None and tt_node.text != 'None') else 0.0

            dist_node = root.find('.//DistanceMeters')
            total_distance = float(dist_node.text) if (dist_node is not None and dist_node.text != 'None') else 0.0

            cal_node = root.find('.//Calories')
            total_calories = int(float(cal_node.text)) if (cal_node is not None and cal_node.text != 'None') else 0

            end_time_ms = start_time_ms + int(total_time_sec_computed * 1000)

            trackpoints = root.findall('.//Trackpoint')
            has_points = len(trackpoints) > 0

            builder = FitFileBuilder(auto_define=True, min_string_size=50)

            sport_type = Sport.RUNNING
            sub_sport = SubSport.GENERIC
            if "Ride" in self.track_type or "Cycling" in self.track_type or tcx_sport == "Biking":
                sport_type = Sport.CYCLING
            elif "Hike" in self.track_type or "Walk" in self.track_type or tcx_sport == "Hiking":
                sport_type = Sport.HIKING
            elif "Training" in self.track_type or "Workout" in self.track_type or tcx_sport == "Other":
                sport_type = Sport.TRAINING

            # 1. Base Information (无论有无轨迹都需要)
            message = FileIdMessage()
            message.type = FileType.ACTIVITY
            message.manufacturer = 1
            message.product = 3415
            message.time_created = start_time_ms
            message.serial_number = 1234567890
            builder.add(message)

            message = DeviceInfoMessage()
            message.serial_number = 1234567890
            message.manufacturer = 1
            message.garmin_product = 3415
            message.software_version = 3.58
            message.device_index = 0
            message.source_type = 5
            builder.add(message)

            message = EventMessage()
            message.event = Event.TIMER
            message.event_type = EventType.START
            message.event_group = 0
            message.timer_trigger = TimerTrigger.MANUAL
            message.timestamp = start_time_ms
            builder.add(message)

            # --- 2. 分支逻辑: 无轨迹直接生成 Summary，有轨迹则调用 tcxreader ---
            if not has_points:
                # Lap Message
                lap_msg = LapMessage()
                lap_msg.timestamp = end_time_ms
                lap_msg.start_time = start_time_ms
                lap_msg.total_elapsed_time = total_time_sec_computed
                lap_msg.total_timer_time = total_time_sec_computed
                lap_msg.total_distance = total_distance
                if total_calories > 0:
                    lap_msg.total_calories = total_calories
                lap_msg.sport = sport_type
                lap_msg.sub_sport = sub_sport
                builder.add(lap_msg)

                message = EventMessage()
                message.event = Event.TIMER
                message.event_type = EventType.STOP_ALL
                message.event_group = 0
                message.timer_trigger = TimerTrigger.MANUAL
                message.timestamp = end_time_ms
                builder.add(message)

                # Session Message
                message = SessionMessage()
                message.timestamp = end_time_ms
                message.start_time = start_time_ms
                message.total_elapsed_time = total_time_sec_computed
                message.total_timer_time = total_time_sec_computed
                message.total_distance = total_distance
                if total_calories > 0:
                    message.total_calories = total_calories
                message.sport = sport_type
                message.sub_sport = sub_sport
                message.trigger = SessionTrigger.ACTIVITY_END
                message.event = Event.SESSION
                message.event_type = EventType.STOP
                builder.add(message)

            else:
                print(f"  [Debug] 进入有轨迹写入模式，调用 tcxreader...")
                tcx = TCXReader().read(self.tcx_path, only_gps=False)
                points = []
                for lap in tcx.laps:
                    for tp in lap.trackpoints:
                        if tp.time:
                            points.append(tp)

                if not points:
                    print("  [Debug] tcxreader 解析后发现没有可用点，跳过！")
                    return False

                start_time_ms = int(points[0].time.timestamp() * 1000)
                end_time_ms = int(points[-1].time.timestamp() * 1000)

                LAP_DISTANCE_TARGET = 1000.0
                total_distance = 0.0
                moving_time = 0.0
                total_calories = sum([lap.calories for lap in tcx.laps if hasattr(lap, 'calories') and lap.calories])
                total_ascent = 0.0

                global_hrs, global_cads, global_powers = [], [], []

                lap_start_time = points[0].time
                lap_start_dist = 0.0
                lap_moving_time = 0.0
                lap_start_coord = None
                lap_hrs, lap_cads, lap_powers = [], [], []
                lap_ascent = 0.0

                prev_coordinate = None
                prev_time = None
                prev_alt = None

                # Iterate over points and construct FIT RecordMessage
                for tp in points:
                    current_coord = (tp.latitude, tp.longitude) if tp.latitude and tp.longitude else None
                    current_time = tp.time
                    current_alt = tp.elevation if hasattr(tp, 'elevation') and tp.elevation is not None else None

                    if prev_coordinate and current_coord and prev_time and current_time:
                        delta = geodesic(prev_coordinate, current_coord).meters
                        time_diff = (current_time - prev_time).total_seconds()
                        if 0 < time_diff < 120:
                            moving_time += time_diff
                            lap_moving_time += time_diff
                            if not hasattr(tp, 'distance') or tp.distance is None:
                                total_distance += delta

                    if hasattr(tp, 'distance') and tp.distance is not None:
                        total_distance = tp.distance

                    if prev_alt is not None and current_alt is not None and current_alt > prev_alt:
                        alt_diff = current_alt - prev_alt
                        total_ascent += alt_diff
                        lap_ascent += alt_diff

                    if not lap_start_coord and current_coord:
                        lap_start_coord = current_coord

                    message = RecordMessage()
                    if current_coord:
                        message.position_lat = tp.latitude
                        message.position_long = tp.longitude
                    message.distance = total_distance
                    if current_alt is not None:
                        message.altitude = current_alt
                    message.timestamp = int(tp.time.timestamp() * 1000)

                    if hasattr(tp, 'hr_value') and tp.hr_value is not None:
                        hr = int(tp.hr_value)
                        hr = min(hr, 255)
                        message.heart_rate = hr
                        global_hrs.append(hr)
                        lap_hrs.append(hr)

                    if hasattr(tp, 'cadence') and tp.cadence is not None:
                        raw_cadence = float(tp.cadence)
                        raw_cadence = min(raw_cadence, 255.0)
                        spm = int(raw_cadence) * 2
                        message.cadence = int(raw_cadence)
                        global_cads.append(spm)
                        lap_cads.append(spm)

                    if hasattr(tp, 'tpx_ext') and tp.tpx_ext:
                        if tp.tpx_ext.get('Watts'):
                            try:
                                pwr = int(float(tp.tpx_ext.get('Watts')))
                                message.power = pwr
                                global_powers.append(pwr)
                                lap_powers.append(pwr)
                            except Exception:
                                pass
                        if tp.tpx_ext.get('StepLength'):
                            try:
                                val = float(tp.tpx_ext.get('StepLength'))
                                if val > 0: message.step_length = val
                            except Exception:
                                pass
                        if tp.tpx_ext.get('StanceTime'):
                            try:
                                val = float(tp.tpx_ext.get('StanceTime'))
                                if val > 0: message.stance_time = val
                            except Exception:
                                pass
                        if tp.tpx_ext.get('VerticalOscillation'):
                            try:
                                val = float(tp.tpx_ext.get('VerticalOscillation'))
                                if val > 0: message.vertical_oscillation = val
                            except Exception:
                                pass

                    builder.add(message)

                    is_last_point = (tp == points[-1])
                    lap_current_dist = total_distance - lap_start_dist

                    if lap_current_dist >= LAP_DISTANCE_TARGET or is_last_point:
                        lap_msg = LapMessage()
                        lap_msg.timestamp = int(tp.time.timestamp() * 1000)
                        lap_msg.start_time = int(lap_start_time.timestamp() * 1000)
                        lap_msg.total_elapsed_time = (tp.time - lap_start_time).total_seconds()
                        lap_msg.total_timer_time = lap_moving_time
                        lap_msg.total_distance = lap_current_dist
                        lap_msg.total_ascent = int(lap_ascent)

                        if lap_start_coord:
                            lap_msg.start_position_lat = lap_start_coord[0]
                            lap_msg.start_position_long = lap_start_coord[1]
                        if current_coord:
                            lap_msg.end_position_lat = current_coord[0]
                            lap_msg.end_position_long = current_coord[1]

                        if lap_hrs:
                            lap_msg.avg_heart_rate = min(int(sum(lap_hrs) / len(lap_hrs)), 255)
                            lap_msg.max_heart_rate = min(max(lap_hrs), 255)
                        if lap_cads:
                            lap_msg.avg_cadence = min(int(sum(lap_cads) / len(lap_cads) / 2), 255)
                            lap_msg.max_cadence = min(int(max(lap_cads) / 2), 255)
                        if lap_powers:
                            lap_msg.avg_power = int(sum(lap_powers) / len(lap_powers))
                            lap_msg.max_power = max(lap_powers)

                        lap_msg.sport = sport_type
                        lap_msg.sub_sport = sub_sport
                        builder.add(lap_msg)

                        lap_start_time = tp.time
                        lap_start_dist = total_distance
                        lap_moving_time = 0.0
                        lap_start_coord = current_coord
                        lap_hrs, lap_cads, lap_powers = [], [], []
                        lap_ascent = 0.0

                    if current_coord: prev_coordinate = current_coord
                    if current_time: prev_time = current_time
                    if current_alt is not None: prev_alt = current_alt

                message = EventMessage()
                message.event = Event.TIMER
                message.event_type = EventType.STOP_ALL
                message.event_group = 0
                message.timer_trigger = TimerTrigger.MANUAL
                message.timestamp = end_time_ms
                builder.add(message)

                message = SessionMessage()
                message.timestamp = end_time_ms
                message.start_time = start_time_ms
                message.total_elapsed_time = (points[-1].time - points[0].time).total_seconds()
                message.total_timer_time = moving_time
                message.total_distance = total_distance
                message.total_ascent = int(total_ascent)

                if global_hrs:
                    message.avg_heart_rate = min(int(sum(global_hrs) / len(global_hrs)), 255)
                    message.max_heart_rate = min(max(global_hrs), 255)
                    message.min_heart_rate = min(min(global_hrs), 255)
                if global_cads:
                    message.avg_cadence = min(int(sum(global_cads) / len(global_cads) / 2), 255)
                    message.max_cadence = min(int(max(global_cads) / 2), 255)
                if global_powers:
                    message.avg_power = int(sum(global_powers) / len(global_powers))
                    message.max_power = max(global_powers)
                if total_calories > 0:
                    message.total_calories = int(total_calories)
                if points and points[0].latitude:
                    message.start_position_lat = points[0].latitude
                    message.start_position_long = points[0].longitude

                message.sport = sport_type
                message.sub_sport = sub_sport
                message.trigger = SessionTrigger.ACTIVITY_END
                message.event = Event.SESSION
                message.event_type = EventType.STOP
                builder.add(message)

            # ==========================================
            # 3. 最终写入 ActivityMessage 并生成 FIT 文件
            # ==========================================
            message = ActivityMessage()
            message.timestamp = end_time_ms
            message.total_timer_time = moving_time if has_points else total_time_sec_computed
            message.num_sessions = 1
            message.type = Activity.MANUAL
            message.event = Event.ACTIVITY
            message.event_type = EventType.STOP
            builder.add(message)

            fit_file = builder.build()
            fit_file.to_file(self.fit_path)
            return True

        except Exception as e:
            print(f"  [Debug] TCX2FIT 发生严重异常: {e}")
            traceback.print_exc()
            return False