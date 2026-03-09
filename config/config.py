import os
from collections import namedtuple

# 获取当前 config.py 所在的目录
current = os.path.dirname(os.path.realpath(__file__))
# 向上退一级，获取真正的项目根目录
ROOT_DIR = os.path.dirname(current)

# 运动数据文件输出路径
OUTPUT_DIR = os.path.join(ROOT_DIR, "outputs")
GPX_FOLDER = os.path.join(OUTPUT_DIR, "GPX_OUT")
TCX_FOLDER = os.path.join(OUTPUT_DIR, "TCX_OUT")
FIT_FOLDER = os.path.join(OUTPUT_DIR, "FIT_OUT")

FOLDER_DICT = {
    "gpx": GPX_FOLDER,
    "tcx": TCX_FOLDER,
    "fit": FIT_FOLDER,
}

# 同步记录文件（去重依赖），放在根目录方便 Git 提交
KEEP2GARMIN_BK_PATH = os.path.join(ROOT_DIR, "keep2garmin.json")

BASE_TIMEZONE = "Asia/Shanghai"
UTC_TIMEZONE = "UTC"

start_point = namedtuple("start_point", "lat lon")
run_map = namedtuple("polyline", "summary_polyline")

# add more type here
TYPE_DICT = {
    "running": "Run",
    "RUN": "Run",
    "Run": "Run",
    "track_running": "Run",
    "trail_running": "Trail Run",
    "cycling": "Ride",
    "CYCLING": "Ride",
    "Ride": "Ride",
    "EBikeRide": "Ride",
    "E-Bike": "Ride",
    "road_biking": "Ride",
    "Road Bike": "Ride",
    "Mountain Bike": "Ride",
    "VirtualRide": "VirtualRide",
    "indoor_cycling": "Indoor Ride",
    "Indoor Bike ": "Indoor Ride",
    "walking": "Hike",
    "hiking": "Hike",
    "Walk": "Hike",
    "Hike": "Hike",
    "Swim": "Swim",
    "swimming": "Swim",
    "Pool Swim": "Swim",
    "Open Water": "Swim",
    "rowing": "Rowing",
    "RoadTrip": "RoadTrip",
    "flight": "Flight",
    "kayaking": "Kayaking",
    "Snowboard": "Snowboard",
    "resort_skiing_snowboarding_ws": "Ski",  # garmin
    "AlpineSki": "Ski",  # strava
    "Ski": "Ski",
    "BackcountrySki": "BackcountrySki",
    "training": "Training",
    "others": "Training",
    "Workout": "Training",
}

MAPPING_TYPE = [
    "Hike",
    "Ride",
    "VirtualRide",
    "Rowing",
    "Run",
    "Trail Run",
    "Swim",
    "RoadTrip",
    "Kayaking",
    "Snowboard",
    "Ski",
    "BackcountrySki",
    "Training",
]

STRAVA_GARMIN_TYPE_DICT = {
    "Hike": "hiking",
    "Run": "running",
    "EBikeRide": "cycling",
    "VirtualRide": "VirtualRide",
    "Walk": "walking",
    "Swim": "swimming",
    "Training": "training",
}
