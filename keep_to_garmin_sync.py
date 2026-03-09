import argparse
import asyncio
import json
import os
import traceback
from collections import namedtuple

from config.config import TCX_FOLDER, OUTPUT_DIR, FIT_FOLDER, KEEP2GARMIN_BK_PATH
from keep.keep_sync import KEEP_SPORT_TYPES, get_all_keep_tracks
from garmin.garmin_sync import Garmin
from tools.tcx2fit import TCX2FITConverter


def run_keep_to_garmin_sync(email, password, keep_sports_data_api):
    if not os.path.exists(KEEP2GARMIN_BK_PATH):
        with open(KEEP2GARMIN_BK_PATH, "w") as f:
            json.dump([], f)
        content = []
    else:
        with open(KEEP2GARMIN_BK_PATH, "r") as f:
            try:
                content = json.loads(f.read())
            except Exception:
                content = []

    old_tracks_ids = [str(a["run_id"]) for a in content]

    # 强制 Keep 吐出 TCX 数据
    _new_tracks = get_all_keep_tracks(
        email, password, old_tracks_ids, keep_sports_data_api, with_gpx=False, with_tcx=True
    )

    new_tracks = []
    for track in _new_tracks:
        expected_tcx = os.path.join(TCX_FOLDER, str(track.id) + ".tcx")
        if track.start_latlng is not None or os.path.exists(expected_tcx):
            file_path = namedtuple("x", "tcx_file_path")(expected_tcx)
        else:
            file_path = namedtuple("x", "tcx_file_path")(None)

        track = namedtuple("y", track._fields + file_path._fields)(*(track + file_path))
        new_tracks.append(track)

    return new_tracks, content


async def debug_upload_to_garmin(client, file_path):
    upload_url_with_ext = f"{client.modern_url}/upload-service/upload/.fit"

    print(f"\n---> [Debug] 开始上传文件: {file_path}")

    try:
        with open(file_path, "rb") as f:
            file_body = f.read()

        files = {"file": (os.path.basename(file_path), file_body, "application/octet-stream")}

        res = await client.req.post(
            upload_url_with_ext,
            files=files,
            headers=client.headers
        )

        if res.status_code in [200, 201, 202]:
            print(f"---> [Debug] 上传成功")
            return True
        elif res.status_code == 409:
            print("---> [Debug] 上传冲突 (409) - 该运动记录在佳明中已存在，跳过。")
            return True
        elif res.status_code == 401 or res.status_code == 403:
            print("---> [Debug] 认证失败 (401/403) - Garmin Secret 已过期失效。")
            return False
        else:
            print(f"---> [Debug] 上传失败，响应体: {res.text[:300]}")
            return False

    except Exception as e:
        print(f"---> [Debug] 发生异常: {str(e)}")
        return False


async def process_and_upload(new_tracks, garmin_secret, is_cn):
    garmin_auth_domain = "CN" if is_cn else "COM"
    client = Garmin(garmin_secret, garmin_auth_domain)

    # 强制创建输出目录，防止云端运行报错
    os.makedirs(FIT_FOLDER, exist_ok=True)
    os.makedirs(TCX_FOLDER, exist_ok=True)

    print(f"共有 {len(new_tracks)} 条记录准备处理并上传至 Garmin...")
    uploaded_tracks = []

    for track in new_tracks:
        if track.tcx_file_path is not None and os.path.exists(track.tcx_file_path):
            fit_file_path = os.path.join(FIT_FOLDER, f"{track.id}.fit")

            try:
                print(f"\n[操作] 正在将 TCX 转换为 FIT: {track.id}")
                converter = TCX2FITConverter(track.tcx_file_path, fit_file_path, track.type)

                if converter.convert():
                    is_success = await debug_upload_to_garmin(client, fit_file_path)

                    if is_success:
                        uploaded_tracks.append(track)
                    else:
                        print(f"[操作] {track.id} 上传失败。")
                else:
                    print(f"[操作] TCX -> FIT 转换失败，跳过 {track.id}")

            except Exception as e:
                print(f"[错误] 处理 {track.id} 发生异常: {e}")
        else:
            # 无 GPS 数据也当做已处理
            uploaded_tracks.append(track)

    await client.req.aclose()
    return uploaded_tracks


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("phone_number", help="keep login phone number")
    parser.add_argument("password", help="keep login password")
    parser.add_argument("garmin_secret", help="garmin secret string")
    parser.add_argument(
        "--sync-types",
        dest="sync_types",
        nargs="+",
        default=["running", "hiking", "cycling","training"],
        help="sync sport types from keep",
    )
    parser.add_argument("--is-cn", dest="is_cn", action="store_true", help="if garmin account is cn")

    options = parser.parse_args()

    for _type in options.sync_types:
        assert _type in KEEP_SPORT_TYPES, f"{_type} is not supported"

    new_tracks, content = run_keep_to_garmin_sync(
        options.phone_number,
        options.password,
        options.sync_types
    )

    loop = asyncio.get_event_loop()
    uploaded_tracks = loop.run_until_complete(
        process_and_upload(new_tracks, options.garmin_secret, options.is_cn)
    )

    # 备份记录并去重
    content.extend([
        dict(run_id=track.id, name=track.name, type=track.type, tcx_file_path=track.tcx_file_path)
        for track in uploaded_tracks
    ])

    with open(KEEP2GARMIN_BK_PATH, "w") as f:
        json.dump(content, f, indent=2)

    # 清理中间文件
    for track in uploaded_tracks:
        if track.tcx_file_path is not None and os.path.exists(track.tcx_file_path):
            os.remove(track.tcx_file_path)
        fit_file_path = os.path.join(FIT_FOLDER, f"{track.id}.fit")
        if os.path.exists(fit_file_path):
            os.remove(fit_file_path)
