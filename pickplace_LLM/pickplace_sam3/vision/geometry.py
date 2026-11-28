"""픽셀 + 깊이 → 카메라 좌표 → 로봇 base 좌표 (Calibration_Tutorial/verify.py 와 같은 계산).

  카메라 좌표 (mm):  x = (u - ppx) * z / fx,  y = (v - ppy) * z / fy,  z = 깊이
  base 좌표       :  base2gripper(현재 posx, ZYZ 오일러) @ gripper2camera(T_gripper2camera.npy) @ [x, y, z, 1]
"""
import warnings

import numpy as np
from scipy.spatial.transform import Rotation

import config

_GRIPPER2CAM = np.load(config.GRIPPER2CAM_FILE)


def depth_at(depth, u, v, window=config.DEPTH_WINDOW):
    """(u, v) 주변 window x window 픽셀에서 0(측정 실패)을 뺀 깊이 중간값(mm). 없으면 None."""
    h, w = depth.shape[:2]
    r = window // 2
    patch = depth[max(0, v - r):min(h, v + r + 1), max(0, u - r):min(w, u + r + 1)]
    valid = patch[patch > 0]
    return float(np.median(valid)) if valid.size else None


def pose_matrix(posx):
    """posx [x, y, z, rx, ry, rz] (mm, deg, ZYZ) → 4x4 변환 행렬"""
    T = np.eye(4)
    T[:3, :3] = Rotation.from_euler("ZYZ", posx[3:6], degrees=True).as_matrix()
    T[:3, 3] = posx[:3]
    return T


def pixel_to_base(u, v, z, intrinsics, robot_posx):
    """픽셀 (u, v) 과 깊이 z(mm) → base 좌표 [x, y, z] (mm). robot_posx 는 사진을 찍을 때의 로봇 자세."""
    cam = np.array([(u - intrinsics["ppx"]) * z / intrinsics["fx"],
                    (v - intrinsics["ppy"]) * z / intrinsics["fy"],
                    z, 1.0])
    return (pose_matrix(robot_posx) @ _GRIPPER2CAM @ cam)[:3]


def depth_to_base_points(depth, intrinsics, robot_posx, step=3):
    """깊이 사진 전체(step 픽셀 간격) → base 좌표 점들 (N, 3) mm. 깊이 0(측정 실패)은 뺀다."""
    h, w = depth.shape[:2]
    vs, us = np.mgrid[0:h:step, 0:w:step]
    z = depth[vs, us].astype(np.float64)
    ok = z > 0
    us, vs, z = us[ok], vs[ok], z[ok]
    cam = np.stack([(us - intrinsics["ppx"]) * z / intrinsics["fx"],
                    (vs - intrinsics["ppy"]) * z / intrinsics["fy"],
                    z, np.ones_like(z)])
    return ((pose_matrix(robot_posx) @ _GRIPPER2CAM) @ cam)[:3].T


def camera_position(robot_posx):
    """사진을 찍을 때 카메라 위치 (base 좌표 mm)"""
    return (pose_matrix(robot_posx) @ _GRIPPER2CAM)[:3, 3]


def yawed(posx, yaw_deg):
    """posx 의 방향을 base z축 기준으로 yaw_deg 만큼 돌린 자세 (위치는 그대로)"""
    R = Rotation.from_euler("z", yaw_deg, degrees=True) * Rotation.from_euler("ZYZ", posx[3:6], degrees=True)
    with warnings.catch_warnings():
        # 그리퍼가 정확히 아래(ry=180)면 ZYZ 가 특이점이라 scipy 가 'Gimbal lock' 경고를 내지만 결과 자세는 맞다
        warnings.simplefilter("ignore", UserWarning)
        angles = R.as_euler("ZYZ", degrees=True)
    return list(posx[:3]) + [float(v) for v in angles]


def tool_z_axis(posx):
    """그리퍼가 향하는 방향(도구 z축)의 base 좌표 단위 벡터"""
    return pose_matrix(posx)[:3, 2]
