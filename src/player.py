import mumuipc
import ldipc


class Player:
    def __init__(self, type_: str, path: str, index: int) -> None:
        self.type = type_
        self.display_id = -1
        if type_ == "mumuv4":
            self.player = mumuipc.MuMuPlayer(path, index, "v4")
        elif type_ == "mumuv5":
            self.player = mumuipc.MuMuPlayer(path, index, "v5")
        elif type_ == "ld":
            self.player = ldipc.LDPlayer(path, index)

    @property
    def resolution(self):
        return self.player.resolution

    def ipc_capture_display(self):
        if self.type.startswith("mumu"):
            if self.display_id == -1:
                self.display_id = self.player.ipc_get_display_id(
                    "com.bilibili.star.bili"
                )
            frame = self.player.ipc_capture_display(self.display_id)
            if frame is None:
                # 2026-10-20 凌晨场:MuMu 掉线时 IPC 返回 None,原代码直接
                # [:, :, :3] 下标报 'NoneType' object is not subscriptable,
                # 从错误看不出是掉线。显式返回 None,由调用方按"抓不到"处理。
                return None
            # RGBA -> RGB
            return frame[:, :, :3]
        else:
            # RGB
            return self.player.capture()
