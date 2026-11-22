"""Challenge-live CP selection on the v1.3.0 release playback engine."""

import json
import logging
import re
import unicodedata

from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition


CP_PER_MULTIPLIER = 200
CP_MULTIPLIERS = (8, 4, 2, 1)
DEFAULT_CP_MULTIPLIER = CP_MULTIPLIERS[0]
CP_ROWS = {1: 177, 2: 250, 4: 323, 8: 396}


def parse_cp_balance(text):
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[\s,，]", "", text)
    return int(text) if re.fullmatch(r"[0-9]+", text) else None


def choose_cp_multiplier(balance, max_multiplier=DEFAULT_CP_MULTIPLIER):
    for multiplier in CP_MULTIPLIERS:
        if multiplier <= max_multiplier and balance >= multiplier * CP_PER_MULTIPLIER:
            return multiplier
    return 0


def click(context, name, target):
    result = context.run_action(
        name,
        pipeline_override={name: {"action": "Click", "target": target, "post_delay": 300}},
    )
    return result is not None and result.completed


class ChallengeCPRecognition(CustomRecognition):
    def analyze(self, context, argv):
        heading = context.run_recognition(
            "_challenge_cp_heading",
            argv.image,
            {"_challenge_cp_heading": {
                "recognition": "TemplateMatch",
                "template": "live/challenge_cp_heading.png",
                "threshold": 0.75,
            }},
        )
        if heading is None or heading.best_result is None:
            return None
        box = heading.best_result.box
        # The attachment is a cropped dialog. Locate it before using its local coordinates.
        x, y = box[0] - 28, box[1] - 28
        balance = context.run_recognition(
            "_challenge_cp_balance",
            argv.image,
            {"_challenge_cp_balance": {
                "recognition": "OCR",
                "roi": [x + 350, y + 88, 156, 32],
            }},
        )
        cp = parse_cp_balance(balance.best_result.text) if balance and balance.best_result else None
        return CustomRecognition.AnalyzeResult(
            [x, y, 611, 647], json.dumps({"cp": cp, "origin": [x, y]})
        )


class SelectChallengeCP(CustomAction):
    def run(self, context, argv):
        detail = argv.reco_detail.best_result.detail
        if isinstance(detail, str):
            detail = json.loads(detail)
        balance = detail["cp"]
        if balance is None:
            logging.error("无法识别挑战点数，停止挑战演出。")
            return False
        settings = json.loads(argv.custom_action_param)
        multiplier = choose_cp_multiplier(balance, settings["cp_multiplier"])
        if multiplier == 0:
            finish = settings["finish"]
            logging.info("剩余 %s CP，不足 200 CP，准备%s。", balance,
                         "退出游戏" if finish == "exit" else "返回主页面")
            x, y = detail["origin"]
            return context.override_pipeline({
                "challenge_cp_dialog": {"next": "close_app" if finish == "exit" else "challenge_finish_home"},
                "challenge_finish_home": {"target": [x + 60, y + 566, 235, 39]},
            })
        cost = multiplier * CP_PER_MULTIPLIER
        x, y = detail["origin"]
        logging.info("剩余 %s CP，本次使用 %s 倍（%s CP）。", balance, multiplier, cost)
        if not click(context, "_challenge_choose_cp", [x + 534, y + CP_ROWS[multiplier] - 10, 20, 20]):
            return False
        # Verify the applied cost on the final-confirmation screen before starting.
        if not context.override_pipeline({"challenge_start": {"expected": rf"(^|[^0-9]){cost}\s*消[耗费費]"}}):
            return False
        return click(context, "_challenge_confirm_cp", [x + 335, y + 566, 200, 39])


def challenge_overrides(finish="home", cp_multiplier=DEFAULT_CP_MULTIPLIER):
    final_page = {"recognition": "OCR", "expected": "最终确认", "roi": [110, 55, 270, 50]}
    song_selection = ["select_song", "challenge_back_to_song"]
    after_result = [*song_selection, "liveagain", "select_live_mode", "live_home_button"]
    return {
        "main": {"next": ["select_song", "challenge_back_to_song", "select_live_mode", "live_home_button"]},
        "select_song": {"next": ["set_difficulty"], "roi": [110, 55, 270, 50]},
        "set_difficulty": {"interrupt": [], "on_error": "stop"},
        "get_song_name": {
            "next": "challenge_confirm_song", "interrupt": [],
            "timeout": 10000, "on_error": "stop",
        },
        "challenge_confirm_song": {
            "recognition": "OCR", "expected": "确定", "roi": [935, 610, 330, 100],
            "action": "Click", "post_delay": 500,
            "next": ["challenge_cp_dialog", "challenge_final"],
            "interrupt": ["login_expired", "connect_failed"],
            "timeout": 10000, "on_error": "stop",
        },
        "challenge_final": {
            **final_page,
            "next": "challenge_cp_settings", "timeout": 10000, "on_error": "stop",
        },
        "challenge_cp_settings": {
            "recognition": "OCR", "expected": "设[置定]", "roi": [790, 620, 125, 78],
            "action": "Click", "post_delay": 500,
            "next": "challenge_cp_dialog", "timeout": 10000, "on_error": "stop",
        },
        "challenge_cp_dialog": {
            "recognition": "Custom", "custom_recognition": "ChallengeCPRecognition",
            "action": "Custom", "custom_action": "SelectChallengeCP",
            "custom_action_param": {"finish": finish, "cp_multiplier": cp_multiplier},
            "next": "ensure_live_mode", "timeout": 10000, "on_error": "stop",
        },
        # 先认挑战「最终确认」页，再用通用节点关闭 3D/MV。扣点核对仍紧挨开演。
        "ensure_live_mode": {
            **final_page,
            "timeout": 10000, "on_error": "stop",
        },
        "disable_liveplay": {"next": "challenge_start"},
        "challenge_finish_home": {
            "action": "Click", "post_delay": 500,
            "next": "to_tome", "on_error": "stop",
        },
        "challenge_start": {
            "recognition": "OCR", "expected": "CP_NOT_SELECTED", "roi": [1020, 532, 170, 52],
            "action": "Custom", "custom_action": "StartChallengeLive",
            "next": "wait_live_start", "post_delay": 2000,
            "timeout": 10000, "on_error": "stop",
        },
        "challenge_back_to_song": {
            **final_page,
            "action": "Click", "target": [30, 36, 46, 46], "post_delay": 500,
            "next": "select_song", "timeout": 10000, "on_error": "stop",
        },
        # 升级模板在白底经验页会误命中，因此只在「再次演出」后确认，可连续处理多张弹窗。
        "liveagain": {"next": ["rankup_confirm", *song_selection]},
        "rankup_confirm": {"next": ["rankup_confirm", *song_selection, "liveagain"]},
        "save_succeed_playresult": {
            "next": ["event_reward_confirm", *after_result],
        },
        # 「下一步」只推进结果页，自身没有后续节点，作为中断才能接着等挑战页面。
        "event_reward_confirm": {
            "next": ["event_reward_ok", *after_result],
            "interrupt": ["next_button"],
        },
        "event_reward_ok": {
            "next": after_result,
            "interrupt": ["next_button"],
        },
    }
