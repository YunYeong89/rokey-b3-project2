"""주문 확인 음성 안내 ROS2 노드 — 카메라/QR 인식 노드가 토픽만 보내면 된다.

구독
  /order/customer_detected  std_msgs/Empty   카메라에 손님이 잡힘
  /order/qr                 std_msgs/String  QR 인식 결과 문자열 (예: "ORDER-0001")
  /order/item_taken         std_msgs/Int32   물품을 집어간 장바구니 번호 (1 또는 2)
  /order/reset              std_msgs/Empty   처음 상태로 (손님이 떠남 등)
발행
  /order/voice              std_msgs/String  방금 말한 문구 (화면 표시용)
  /order/state              std_msgs/String  현재 상태: 대기 / QR 대기 / 수령 대기

  source ~/cobot2_ws/install/setup.bash
  python3 order_voice_node.py
"""
import rclpy
from rclpy.node import Node
from std_msgs.msg import Empty, Int32, String

from logic.order_pickup import OrderPickupVoice
from voice.speaker import Speaker


class _PublishingSpeaker(Speaker):
    """말할 때마다 /order/voice 로 문구도 발행"""
    def __init__(self, publish):
        super().__init__()
        self._publish = publish

    def say(self, key, **fmt):
        text, _ = self.prepare(key, **fmt)
        self._publish(text)
        return super().say(key, **fmt)


class OrderVoiceNode(Node):
    def __init__(self):
        super().__init__("order_voice_node")
        self.voice_pub = self.create_publisher(String, "/order/voice", 10)
        self.state_pub = self.create_publisher(String, "/order/state", 10)
        speaker = _PublishingSpeaker(lambda t: self.voice_pub.publish(String(data=t)))
        self.pickup = OrderPickupVoice(speaker)

        for key, fmt in speaker.all_messages():   # 시작할 때 음성 파일을 미리 만들어 둔다(첫 응답 지연 방지)
            speaker.prepare(key, **fmt)

        self.create_subscription(Empty, "/order/customer_detected", lambda _: self._run(self.pickup.customer_detected), 10)
        self.create_subscription(String, "/order/qr", lambda m: self._run(self.pickup.qr_scanned, m.data), 10)
        self.create_subscription(Int32, "/order/item_taken", lambda m: self._run(self.pickup.item_taken, m.data), 10)
        self.create_subscription(Empty, "/order/reset", lambda _: self._run(self.pickup.reset), 10)
        self._publish_state()
        self.get_logger().info("order_voice_node 준비 완료 — /order/* 토픽 대기 중")

    def _run(self, func, *args):
        try:
            func(*args)
        except Exception as e:
            self.get_logger().error(f"음성 처리 오류: {e}")
        self._publish_state()

    def _publish_state(self):
        self.state_pub.publish(String(data=self.pickup.state))


def main():
    rclpy.init()
    node = OrderVoiceNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
