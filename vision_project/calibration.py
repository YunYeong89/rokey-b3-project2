"""hand-eye 보정 결과(corecode/Calibration_Tutorial)와 그 보정 때 쓴 TCP·공구 이름.
handeye_calibration.py가 저장한 T_gripper2camera.npy는 TCP 좌표계에서 본 카메라 자세(mm)이고, data_recording.py에서 자세를
기록할 때 켜 둔 TCP 기준입니다. 그래서 실행 중에도 같은 TCP의 posx로 verify.py와 똑같이 계산해야 base 좌표가 맞습니다:
    base 점 = posx(ZYZ) 행렬 @ T_gripper2camera @ [x, y, z, 1]   (x, y, z = 컬러 intrinsics·정렬 depth로 구한 카메라 좌표, mm)
보정을 다시 하면 resource/T_gripper2camera.npy를 덮어쓰고, 아래 이름을 data_recording.py의 set_tcp·set_tool과 같게 둡니다."""

from pathlib import Path

from ament_index_python.packages import get_package_share_directory
import numpy as np

TCP_NAME, TOOL_NAME = "GripperDA_v1", "Tool Weight"  # data_recording.py의 set_tcp(…)·set_tool(…)과 같게
FILE = Path(get_package_share_directory("vision_project")) / "resource" / "T_gripper2camera.npy"


def load():
    """T_gripper2camera (4, 4), mm."""
    return np.load(FILE)
