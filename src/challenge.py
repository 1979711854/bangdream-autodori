"""Challenge-live CP selection on the v1.3.0 release playback engine."""

import json
import logging
import re
import unicodedata

from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition


CP_PER_MULTIPLIER = 200
CP_ROWS = {1: 177, 2: 250, 4: 323, 8: 396}


def parse_cp_balance(text):
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[\s,，]", "", text)
    return int(text) if re.fullmatch(r"[0-9]+", text) else None


def choose_cp_multiplier(balance):
    return next((m for m in (8, 4, 2, 1) if balance >= m * CP_PER_MULTIPLIER), 0)


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
        multiplier = choose_cp_multiplier(balance)
        if multiplier == 0:
            finish = json.loads(argv.custom_action_param)["finish"]
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


def challenge_overrides(finish="home"):
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
            "recognition": "OCR", "expected": "最终确认", "roi": [110, 55, 270, 50],
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
            "custom_action_param": {"finish": finish},
            "next": "challenge_start", "timeout": 10000, "on_error": "stop",
        },
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
            "recognition": "OCR", "expected": "最终确认", "roi": [110, 55, 270, 50],
            "action": "Click", "target": [30, 36, 46, 46], "post_delay": 500,
            "next": "select_song", "timeout": 10000, "on_error": "stop",
        },
        "liveagain": {"next": ["select_song", "challenge_back_to_song"]},
        "save_succeed_playresult": {
            "next": ["event_reward_confirm", "select_song", "challenge_back_to_song", "liveagain", "select_live_mode", "live_home_button"],
        },
        "event_reward_confirm": {
            "next": ["select_song", "challenge_back_to_song", "liveagain", "select_live_mode", "live_home_button"],
        },
    }
