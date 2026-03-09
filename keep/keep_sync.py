import argparse
import base64
import json
import os
import time
import zlib
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from xml.dom import minidom
import eviltransform
import gpxpy
import polyline
import requests
from config.config import GPX_FOLDER, TCX_FOLDER, run_map, start_point
from Crypto.Cipher import AES
from util.utils import adjust_time
import xml.etree.ElementTree as ET

KEEP_SPORT_TYPES = ["running", "hiking", "cycling"]
KEEP2STRAVA = {
    "outdoorWalking": "Walk",
    "outdoorRunning": "Run",
    "outdoorCycling": "Ride",
    "indoorRunning": "VirtualRun",
    "mountaineering": "Hiking",
}
KEEP2TCX = {
    "outdoorWalking": "Walking",
    "outdoorRunning": "Running",
    "outdoorCycling": "Biking",
    "indoorRunning": "Running",
    "mountaineering": "Hiking",
}

# need to test
LOGIN_API = "https://api.gotokeep.com/v1.1/users/login"
RUN_DATA_API = "https://api.gotokeep.com/pd/v3/stats/detail?dateUnit=all&type={sport_type}&lastDate={last_date}"
RUN_LOG_API = "https://api.gotokeep.com/pd/v3/{sport_type}log/{run_id}"
GRAPH_API = "https://api.gotokeep.com/minnow-webapp/v1/sportlog/sportData/chart/{log_id}?itemCount=2000"

HR_FRAME_THRESHOLD_IN_DECISECOND = 100
TIMESTAMP_THRESHOLD_IN_DECISECOND = 3_600_000
TRANS_GCJ02_TO_WGS84 = True


def login(session, mobile, password):
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:78.0) Gecko/20100101 Firefox/78.0",
        "Content-Type": "application/x-www-form-urlencoded;charset=utf-8",
    }
    data = {"mobile": mobile, "password": password}
    r = session.post(LOGIN_API, headers=headers, data=data)
    if r.ok:
        token = r.json()["data"]["token"]
        headers["Authorization"] = f"Bearer {token}"
        return session, headers


def get_to_download_runs_ids(session, headers, sport_type):
    last_date = 0
    result = []

    while 1:
        r = session.get(
            RUN_DATA_API.format(sport_type=sport_type, last_date=last_date),
            headers=headers,
        )
        if r.ok:
            run_logs = r.json()["data"]["records"]

            for i in run_logs:
                logs = [j["stats"] for j in i["logs"]]
                result.extend(k["id"] for k in logs if not k["isDoubtful"])
            last_date = r.json()["data"]["lastTimestamp"]
            since_time = datetime.fromtimestamp(last_date // 1000, tz=timezone.utc)
            print(f"pares keep ids data since {since_time}")
            time.sleep(1)  # spider rule
            if not last_date:
                break
    return result


def get_single_run_data(session, headers, run_id, sport_type):
    r = session.get(
        RUN_LOG_API.format(sport_type=sport_type, run_id=run_id), headers=headers
    )
    if r.ok:
        return r.json()


def get_chart_data(session, headers, log_id, item_count=2000):
    """
        Fetch advanced chart data from Keep API (e.g., GCT, Vertical Oscillation, Power).
    """
    try:
        r = session.get(
            GRAPH_API.format(log_id=log_id, item_count=item_count), headers=headers
        )
        if r.ok:
            time.sleep(0.5)  # Rate limiting to prevent ban
            return r.json()
        else:
            print(f"Failed to fetch chart data: HTTP {r.status_code}")
            return None
    except Exception as e:
        print(f"Chart API request error: {str(e)}")
        return None


def decode_runmap_data(text, is_geo=False):
    _bytes = base64.b64decode(text)
    key = "NTZmZTU5OzgyZzpkODczYw=="
    iv = "MjM0Njg5MjQzMjkyMDMwMA=="
    if is_geo:
        cipher = AES.new(base64.b64decode(key), AES.MODE_CBC, base64.b64decode(iv))
        _bytes = cipher.decrypt(_bytes)
    run_points_data = zlib.decompress(_bytes, 16 + zlib.MAX_WBITS)
    run_points_data = json.loads(run_points_data)
    return run_points_data


def find_nearest_hr(hr_data_list, target_time, start_time, threshold=HR_FRAME_THRESHOLD_IN_DECISECOND):
    closest_element = None
    min_difference = float("inf")
    if target_time > TIMESTAMP_THRESHOLD_IN_DECISECOND:
        target_time = target_time = target_time - start_time // 100

    for item in hr_data_list:
        timestamp = item.get("timestamp")
        if not timestamp:
            continue
        difference = abs(timestamp - target_time)
        if difference <= threshold and difference < min_difference:
            closest_element = item
            min_difference = difference

    if closest_element:
        hr = closest_element.get("beatsPerMinute")
        if hr and hr > 0:
            return hr
    return None


def find_nearest_val(data_list, target_time, start_time, val_key, threshold=100):
    closest_element = None
    min_difference = float("inf")
    if target_time > 3_600_000:
        target_time = target_time - start_time // 100

    for item in data_list:
        timestamp = item.get("timestamp")
        if not timestamp: continue
        difference = abs(timestamp - target_time)
        if difference <= threshold and difference < min_difference:
            closest_element = item
            min_difference = difference

    if closest_element:
        val = closest_element.get(val_key)

        # Fallback aliases for cadence
        if not val and val_key in ["stepFreq", "cadence"]:
            val = closest_element.get("value") or closest_element.get("stepsPerMinute") or closest_element.get("sr")
        # Fallback for power
        elif not val and val_key == "power":
            val = closest_element.get("value")

        if val and val > 0: return val
    return None


def parse_raw_data_to_nametuple(
        run_data, old_gpx_ids, old_tcx_ids, with_gpx=False, with_tcx=False, session=None, headers=None
):
    run_data = run_data["data"]
    run_points_data = []

    full_log_id = run_data["id"]
    keep_id = run_data["id"].split("_")[1]
    start_time = run_data["startTime"]
    avg_heart_rate = None
    elevation_gain = None
    decoded_hr_data = []

    print(f"Processing Keep Activity ID: {keep_id}")

    # Fetch advanced dynamic chart metrics (e.g., specific to smartwatch sync)
    chart_metrics = {}
    if session and headers:
        chart_data = get_chart_data(session, headers, full_log_id)
        if chart_data and "data" in chart_data:
            for metric_name, metric_data in chart_data["data"].items():
                if isinstance(metric_data, list):
                    chart_metrics[metric_name] = {}
                    for point in metric_data:
                        # Convert X-axis (seconds) to deciseconds for GPS alignment
                        rel_time_deci = int(point.get("x", 0) * 10)
                        value = (point["min"] + point["max"]) / 2 if "min" in point and "max" in point else point.get(
                            "y", 0)
                        chart_metrics[metric_name][rel_time_deci] = value

    if run_data["heartRate"]:
        avg_heart_rate = run_data["heartRate"].get("averageHeartRate", None)
        heart_rate_data = run_data["heartRate"].get("heartRates", None)
        if heart_rate_data:
            decoded_hr_data = decode_runmap_data(heart_rate_data)
        if avg_heart_rate and avg_heart_rate < 0:
            avg_heart_rate = None

    # Fallback step data decryption for standard App usage
    step_raw_data = run_data.get("stepFrequencies") or run_data.get("stepPoints") or (
        run_data.get("step", {}).get("stepFreqs") if run_data.get("step") else None)
    decoded_step_data = []
    if step_raw_data:
        try:
            decoded_step_data = decode_runmap_data(step_raw_data)

            # Dynamically calculate cadence (SPM) if only cumulative steps are provided (e.g., OPPO watches)
            prev_steps = 0
            prev_time = 0.0
            for item in decoded_step_data:
                val = item.get("stepFreq") or item.get("value") or item.get("stepsPerMinute") or item.get("sr")
                if not val and item.get("currentTotalSteps") is not None:
                    curr_steps = item.get("currentTotalSteps", 0)
                    curr_time = item.get("currentTotalDuration", 0.0)  # 单位是秒
                    if curr_time > prev_time:
                        spm = (curr_steps - prev_steps) / (curr_time - prev_time) * 60
                        item["sr"] = int(spm)
                    else:
                        item["sr"] = 0
                    prev_steps = curr_steps
                    prev_time = curr_time
        except Exception:
            pass

    decoded_power_data = []
    power_raw_data = run_data.get("powerPoints") or (
        run_data.get("power", {}).get("powers") if run_data.get("power") else None)
    if power_raw_data:
        try:
            decoded_power_data = decode_runmap_data(power_raw_data)
        except Exception:
            pass

    if run_data["geoPoints"]:
        run_points_data = decode_runmap_data(run_data["geoPoints"], True)
        run_points_data_gpx = run_points_data

        if TRANS_GCJ02_TO_WGS84:
            run_points_data = [
                list(eviltransform.gcj2wgs(p["latitude"], p["longitude"]))
                for p in run_points_data
            ]
            for i, p in enumerate(run_points_data_gpx):
                p["latitude"] = run_points_data[i][0]
                p["longitude"] = run_points_data[i][1]

        for p in run_points_data_gpx:
            if "timestamp" not in p:
                p["timestamp"] = p.get("unixTimestamp", 0)

            p_hr = find_nearest_hr(decoded_hr_data, int(p["timestamp"]), start_time)
            if p_hr: p["hr"] = p_hr

            # Map fallback decoded data
            p_cadence = find_nearest_val(decoded_step_data, int(p["timestamp"]), start_time, "stepFreq")
            if p_cadence: p["cadence"] = p_cadence

            p_sa = find_nearest_val(decoded_step_data, int(p["timestamp"]), start_time, "sa")
            if p_sa: p["sa"] = p_sa

            p_gctd = find_nearest_val(decoded_step_data, int(p["timestamp"]), start_time, "gctd")
            if p_gctd: p["gctd"] = p_gctd

            p_power = find_nearest_val(decoded_power_data, int(p["timestamp"]), start_time, "power")
            if p_power: p["power"] = p_power

            # Map advanced chart data (Overrides fallback data if exists)
            if chart_metrics:
                if p["timestamp"] > 3_600_000:
                    rel_time_deci = int(p["timestamp"] - start_time // 100)
                else:
                    rel_time_deci = int(p["timestamp"])

                for metric_name, metric_data in chart_metrics.items():
                    closest_time = None
                    min_diff = float("inf")
                    # Allow a 15-second matching window
                    for chart_time in metric_data.keys():
                        diff = abs(chart_time - rel_time_deci)
                        if diff <= 150 and diff < min_diff:
                            min_diff = diff
                            closest_time = chart_time

                    if closest_time is not None:
                        val = metric_data[closest_time]
                        # 【关键修复】：严格过滤掉 Keep 用于占位的负数或 0 (比如 -10, -40)
                        if val is not None and val > 0:
                            # 兼容不同设备的字段名，填入点位中
                            if metric_name in ["cadence", "踏频", "stepFreq"]:
                                p["cadence"] = val
                            elif metric_name in ["power", "功率"]:
                                p["power"] = val
                            elif metric_name in ["groundContactTime", "触地时间", "gctd"]:
                                p["gctd"] = val
                            elif metric_name in ["stepLength", "步幅", "sa"]:
                                p["sa"] = val
                            elif metric_name in ["verticalOscillation", "垂直振幅", "vo"]:
                                p["vo"] = val

        if (run_data["dataType"].startswith("outdoor") or run_data["dataType"] == "mountaineering"):
            if with_gpx:
                gpx_data = parse_points_to_gpx(
                    run_points_data_gpx, start_time, KEEP2STRAVA[run_data["dataType"]]
                )
                elevation_gain = gpx_data.get_uphill_downhill().uphill
                if str(keep_id) not in old_gpx_ids:
                    download_keep_gpx(gpx_data.to_xml(), str(keep_id))
            if with_tcx:
                tcx_data = parse_points_to_tcx(
                    run_data, run_points_data_gpx, KEEP2TCX[run_data["dataType"]]
                )
                if str(keep_id) not in old_tcx_ids:
                    download_keep_tcx(tcx_data.toprettyxml(), str(keep_id))
    else:
        print(f"ID {keep_id} no gps data")

    polyline_str = polyline.encode(run_points_data) if run_points_data else ""
    start_latlng = start_point(*run_points_data[0]) if run_points_data else None
    start_date = datetime.fromtimestamp(start_time // 1000, tz=timezone.utc)
    tz_name = run_data.get("timezone", "")
    start_date_local = adjust_time(start_date, tz_name)
    end = datetime.fromtimestamp(run_data["endTime"] // 1000, tz=timezone.utc)
    end_local = adjust_time(end, tz_name)

    if not run_data["duration"]: return

    d = {
        "id": int(keep_id),
        "name": f"{KEEP2STRAVA[run_data['dataType']]} from keep",
        "type": f"{KEEP2STRAVA[(run_data['dataType'])]}",
        "subtype": f"{KEEP2STRAVA[(run_data['dataType'])]}",
        "start_date": datetime.strftime(start_date, "%Y-%m-%d %H:%M:%S"),
        "end": datetime.strftime(end, "%Y-%m-%d %H:%M:%S"),
        "start_date_local": datetime.strftime(start_date_local, "%Y-%m-%d %H:%M:%S"),
        "end_local": datetime.strftime(end_local, "%Y-%m-%d %H:%M:%S"),
        "length": run_data["distance"],
        "average_heartrate": int(avg_heart_rate) if avg_heart_rate else None,
        "map": run_map(polyline_str),
        "start_latlng": start_latlng,
        "distance": run_data["distance"],
        "moving_time": timedelta(seconds=run_data["duration"]),
        "elapsed_time": timedelta(
            seconds=int((run_data["endTime"] - run_data["startTime"]) // 1000)
        ),
        "average_speed": run_data["distance"] / run_data["duration"],
        "elevation_gain": elevation_gain,
        "location_country": str(run_data.get("region", "")),
        "source": "Keep",
    }
    return namedtuple("x", d.keys())(*d.values())


def get_all_keep_tracks(email, password, old_tracks_ids, keep_sports_data_api, with_gpx=False, with_tcx=False, ):
    if with_gpx and not os.path.exists(GPX_FOLDER): os.makedirs(GPX_FOLDER, exist_ok=True)
    if with_tcx and not os.path.exists(TCX_FOLDER): os.makedirs(TCX_FOLDER, exist_ok=True)

    s = requests.Session()
    s, headers = login(s, email, password)
    tracks = []

    for api in keep_sports_data_api:
        runs = get_to_download_runs_ids(s, headers, api)
        runs = [run for run in runs if run.split("_")[1] not in old_tracks_ids]
        print(f"Found {len(runs)} new keep {api} activities to sync.")

        old_gpx_ids = [i.split('.')[0] for i in os.listdir(GPX_FOLDER) if not i.startswith('.')] if with_gpx else []
        old_tcx_ids = [i.split('.')[0] for i in os.listdir(TCX_FOLDER) if not i.startswith('.')] if with_tcx else []

        for run in runs:
            try:
                run_data = get_single_run_data(s, headers, run, api)
                track = parse_raw_data_to_nametuple(
                    run_data, old_gpx_ids, old_tcx_ids, with_gpx, with_tcx, s, headers
                )
                if track: tracks.append(track)
            except Exception as e:
                print(f"Something wrong paring keep id {run}: " + str(e))
    return tracks


def parse_points_to_gpx(run_points_data, start_time, sport_type):
    points_dict_list = []
    if (
            run_points_data
            and run_points_data[0]["timestamp"] > TIMESTAMP_THRESHOLD_IN_DECISECOND
    ):
        start_time = 0

    for point in run_points_data:
        points_dict = {
            "latitude": point["latitude"],
            "longitude": point["longitude"],
            "time": datetime.fromtimestamp(
                (start_time // 1000 + point["timestamp"] // 10),
                tz=timezone.utc,
            ),
            "elevation": point.get("altitude"),
            "hr": point.get("hr"),
        }
        points_dict_list.append(points_dict)
    gpx = gpxpy.gpx.GPX()
    gpx.nsmap["gpxtpx"] = "http://www.garmin.com/xmlschemas/TrackPointExtension/v1"
    gpx_track = gpxpy.gpx.GPXTrack()
    gpx_track.name = "gpx from keep"
    gpx_track.type = sport_type
    gpx.tracks.append(gpx_track)

    gpx_segment = gpxpy.gpx.GPXTrackSegment()
    gpx_track.segments.append(gpx_segment)
    for p in points_dict_list:
        point = gpxpy.gpx.GPXTrackPoint(
            latitude=p["latitude"],
            longitude=p["longitude"],
            time=p["time"],
            elevation=p.get("elevation"),
        )
        if p.get("hr") is not None:
            gpx_extension_hr = ET.fromstring(
                f"""<gpxtpx:TrackPointExtension xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">
                    <gpxtpx:hr>{p["hr"]}</gpxtpx:hr>
                    </gpxtpx:TrackPointExtension>
                    """
            )
            point.extensions.append(gpx_extension_hr)
        gpx_segment.points.append(point)
    return gpx


def parse_points_to_tcx(run_data, run_points_data, sport_type):
    fit_start_time = datetime.fromtimestamp(
        run_data.get("startTime") // 1000, tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    training_center_database = ET.Element(
        "TrainingCenterDatabase",
        {
            "xmlns": "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2",
            "xmlns:ns5": "http://www.garmin.com/xmlschemas/ActivityGoals/v1",
            "xmlns:ns3": "http://www.garmin.com/xmlschemas/ActivityExtension/v2",
            "xmlns:ns2": "http://www.garmin.com/xmlschemas/UserProfile/v2",
            "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance",
            "xmlns:ns4": "http://www.garmin.com/xmlschemas/ProfileExtension/v1",
            "xsi:schemaLocation": "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2 http://www.garmin.com/xmlschemas/TrainingCenterDatabasev2.xsd",
        },
    )
    ET.ElementTree(training_center_database)
    activities = ET.Element("Activities")
    training_center_database.append(activities)
    activity = ET.Element("Activity", {"Sport": sport_type})
    activities.append(activity)

    activity_id = ET.Element("Id")
    activity_id.text = fit_start_time
    activity.append(activity_id)

    activity_lap = ET.Element("Lap", {"StartTime": fit_start_time})
    activity.append(activity_lap)

    activity_total_time = ET.Element("TotalTimeSeconds")
    activity_total_time.text = str(run_data.get("duration"))
    activity_lap.append(activity_total_time)

    activity_distance = ET.Element("DistanceMeters")
    activity_distance.text = str(run_data.get("distance"))
    activity_lap.append(activity_distance)

    activity_calories = ET.Element("Calories")
    activity_calories.text = str(run_data.get("calorie"))
    activity_lap.append(activity_calories)

    track = ET.Element("Track")
    activity_lap.append(track)

    for point in run_points_data:
        tp = ET.Element("Trackpoint")
        track.append(tp)
        time_stamp = datetime.fromtimestamp(
            (run_data.get("startTime") // 1000 + point.get("timestamp") // 10),
            tz=timezone.utc,
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        time_label = ET.Element("Time")
        time_label.text = time_stamp
        tp.append(time_label)

        try:
            position = ET.Element("Position")
            tp.append(position)
            lati = ET.Element("LatitudeDegrees")
            lati.text = str(point["latitude"])
            position.append(lati)
            longi = ET.Element("LongitudeDegrees")
            longi.text = str(point["longitude"])
            position.append(longi)
            altitude_meters = ET.Element("AltitudeMeters")
            altitude_meters.text = str(point.get("altitude"))
            tp.append(altitude_meters)
        except KeyError:
            pass

        try:
            bpm = ET.Element("HeartRateBpm")
            bpm_value = ET.Element("Value")
            bpm.append(bpm_value)
            bpm_value.text = str(point["hr"])
            tp.append(bpm)
        except KeyError:
            pass

        if point.get("cadence"):
            try:
                cadence_node = ET.Element("Cadence")
                cadence_node.text = str(int(point["cadence"]) // 2)
                tp.append(cadence_node)
            except Exception:
                pass

        # Write Running Dynamics to Garmin TCX Extensions
        if point.get("power") or point.get("cadence") or point.get("sa") or point.get("gctd") or point.get(
                "vo"):
            try:
                extensions = ET.Element("Extensions")
                tpx = ET.Element("ns3:TPX")

                if point.get("power"):
                    watts = ET.Element("ns3:Watts")
                    watts.text = str(int(point["power"]))
                    tpx.append(watts)

                if point.get("cadence"):
                    run_cadence = ET.Element("ns3:RunCadence")
                    run_cadence.text = str(int(point["cadence"]))
                    tpx.append(run_cadence)

                if point.get("sa"):
                    step_length = ET.Element("ns3:StepLength")
                    val = float(point["sa"])
                    # Convert to millimeters (mm)
                    if val < 5:
                        val = val * 1000  # e.g., 0.9m -> 900mm
                    elif val < 300:
                        val = val * 10  # e.g., 90cm -> 900mm
                    step_length.text = str(int(val))
                    tpx.append(step_length)

                if point.get("gctd"):
                    stance_time = ET.Element("ns3:StanceTime")
                    stance_time.text = str(int(float(point["gctd"])))
                    tpx.append(stance_time)

                if point.get("vo"):
                    vertical_oscillation = ET.Element("ns3:VerticalOscillation")
                    val = float(point["vo"])
                    if val < 50: val = val * 10  # 如果是厘米(8.5cm) -> 85mm
                    vertical_oscillation.text = str(int(val))
                    tpx.append(vertical_oscillation)

                extensions.append(tpx)
                tp.append(extensions)
            except Exception:
                pass

    xml_str = minidom.parseString(ET.tostring(training_center_database))
    return xml_str


def download_keep_gpx(gpx_data, keep_id):
    try:
        print(f"downloading keep_id {str(keep_id)} gpx")
        file_path = os.path.join(GPX_FOLDER, str(keep_id) + ".gpx")
        with open(file_path, "w") as fb:
            fb.write(gpx_data)
        return file_path
    except Exception as e:
        print(f"Error downloading GPX for ID {keep_id}: {str(e)}")


def download_keep_tcx(tcx_data, keep_id):
    try:
        print(f"downloading keep_id {str(keep_id)} tcx")
        file_path = os.path.join(TCX_FOLDER, str(keep_id) + ".tcx")
        with open(file_path, "w") as fb:
            fb.write(tcx_data)
        return file_path
    except Exception as e:
        print(f"Error downloading TCX for ID {keep_id}: {str(e)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phone_number", help="keep login phone number")
    parser.add_argument("password", help="keep login password")
    parser.add_argument(
        "--sync-types",
        dest="sync_types",
        nargs="+",
        default=KEEP_SPORT_TYPES,
        help="sync sport types from keep, default is running, you can choose from running, hiking, cycling",
    )
    parser.add_argument(
        "--with-gpx",
        dest="with_gpx",
        action="store_true",
        help="get all keep data to gpx and download",
    )
    parser.add_argument(
        "--with-tcx",
        dest="with_tcx",
        action="store_true",
        help="get all keep data to tcx and download",
    )
    options = parser.parse_args()
    for _tpye in options.sync_types:
        assert (
                _tpye in KEEP_SPORT_TYPES
        ), f"{_tpye} are not supported type, please make sure that the type entered in the {KEEP_SPORT_TYPES}"

    old_tcx_ids = [i.split('.')[0] for i in os.listdir(TCX_FOLDER) if
                   not i.startswith('.')] if options.with_tcx and os.path.exists(TCX_FOLDER) else []
    old_gpx_ids = [i.split('.')[0] for i in os.listdir(GPX_FOLDER) if
                   not i.startswith('.')] if options.with_gpx and os.path.exists(GPX_FOLDER) else []
    old_ids = list(set(old_tcx_ids + old_gpx_ids))

    get_all_keep_tracks(
        options.phone_number,
        options.password,
        old_ids,
        options.sync_types,
        options.with_gpx,
        options.with_tcx,
    )
