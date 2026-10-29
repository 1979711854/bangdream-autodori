# -*- coding: utf-8 -*-
r"""「阅读角色故事」专用流程(2026-10-25 新增)。

需求口径(用户定,勿改):
- 分工:用户负责前置 —— 在游戏里把成员按「小故事未读」(或「回忆小故事未读」)
  筛选好,停在「乐队-培养」列表页,再从 GUI 发起;bot 只负责按顺序读。
- 列表筛选**实时生效**:读完一个角色列表就少一个 ⇒ bot 每次都点左上角第一个
  头像;点完头像等不到成员详情(下一节点超时)即视为全部读完,任务自然结束。
- 单角色流程:点头像 → 成员详情点「小故事」/「回忆小故事」(由 --story-type
  决定,通过 story_overrides 换模板) → 「小故事解锁」弹窗点「解锁」(每篇消耗
  两种道具 500+300,实机取证 _story_3) → 「已解锁」弹窗点「无语音」(零下载)
  → 剧情「菜单」→「跳过」→ 确认「跳过」→ 「小故事达成奖励」弹窗点「确定」
  → 成员详情点返回箭头 → 回列表 → 下一个。
- 奖励只点掉、不校验数值:奖励内容会变(卡片/星石),没有可推的关系。
- **每轮固定 50 篇**(用户 2026-10-25 定):到量就暂停,若还有未读,
  再次点击继续;上限只是 config.yml story.max_stories 兜底,GUI 不暴露。
- **回忆小故事入口已砍**(用户定:解锁消耗太多不合算),GUI 只暴露小故事;
  后端 --story-type memoir 保留供命令行调试。
- rank up 弹窗(编队等级提升)与奖励弹窗是**同款「确定」按钮资产**
  (实测 0.9968),由 story_reward_dialog 一个节点靠放宽 roi 全覆盖。

模板取证:2026-10-25 实机 IPC 截图(_story_1~10,根目录 gitignore)。
⚠ 坐标换算教训:目测坐标是截图显示比例,与实际 1280x720 差 1.185 倍,
第一版模板因此整体错位、裁到平坦区,产生 CCOEFF=1.0 的退化假命中
(详见 _exp_crop_story.py 头注)。菜单/跳过/确认跳过复用生产模板
(common/reader/menu.png、common/reader/skip.png、common/button/skip/pink.png),
实测分 0.9989/1.0/0.9998。
"""
import json
import logging

from challenge import click
from maa.custom_action import CustomAction


# 「乐队-培养」列表左上角第一格成员头像(7 列网格起点,见 _story_1 取证;
# 实测格子约 123x123,起点 (292,204))。读哪个角色 = 永远点这一格。
AVATAR_CELL = [292, 204, 123, 123]

# 每轮阅读上限(用户 2026-10-25 定死 50;config.yml story.max_stories 可调,
# GUI 不暴露此配置)
DEFAULT_MAX_STORIES = 50

# 进程内阅读计数(每次启动 bot 归零)
_STORY_READ_COUNT = 0

# 「小故事解锁」材料不足标记(用户 2026-10-25 定:材料不够也暂停)
_STORY_INSUFFICIENT = False


def story_read_count():
    return _STORY_READ_COUNT


def story_insufficient():
    return _STORY_INSUFFICIENT


class StoryAvatarClick(CustomAction):
    """点「乐队-培养」列表左上角第一个成员头像(主循环步进)。

    放成 Custom 而不是管线 Click,是为了拿两样管线给不了的东西:
    ① 每个角色的进度日志(用户能在 GUI 看到"读到第几个");
    ② max_stories 硬上限 —— 收工判据(等不到成员详情)若因界面变化失效,
       没有上限就会无限重读第一个角色。返回 False → on_error: stop 停任务。
    """

    def run(self, context, argv: CustomAction.RunArg):
        global _STORY_READ_COUNT
        try:
            param = json.loads(argv.custom_action_param) if argv.custom_action_param else {}
        except (TypeError, ValueError):
            param = {}
        stype = param.get("story_type", "member")
        limit = int(param.get("max_stories", 50))
        if _STORY_READ_COUNT >= limit:
            # 到量暂停;收尾汇总日志(_main_impl 里)统一按用户口径输出,
            # 这里不再重复打。
            return False
        _STORY_READ_COUNT += 1
        logging.info("开始阅读第 %d 个角色的%s故事",
                     _STORY_READ_COUNT, "小" if stype == "member" else "回忆")
        if not click(context, "_story_avatar_cell", AVATAR_CELL):
            logging.error("点击成员头像失败,停止阅读。")
            return False
        return CustomAction.RunResult(True)


class StoryRewardOk(CustomAction):
    """点「小故事达成奖励」弹窗的「确定」并记完成日志(用户定的口径)。

    这是唯一能确认「一篇真正读完」的位置 —— 奖励弹窗每篇必弹;
    rank up 弹窗与它同款「确定」按钮资产,本节点一并处理(见 story.json)。
    """

    def run(self, context, argv: CustomAction.RunArg):
        try:
            param = json.loads(argv.custom_action_param) if argv.custom_action_param else {}
        except (TypeError, ValueError):
            param = {}
        stype = param.get("story_type", "member")
        box = argv.box
        if not click(context, "_story_reward_click", [box.x, box.y, box.w, box.h]):
            logging.error("点击奖励弹窗「确定」失败,停止阅读。")
            return False
        logging.info("第 %d 篇%s故事已完成,领取奖励。",
                     _STORY_READ_COUNT, "小" if stype == "member" else "回忆")
        return CustomAction.RunResult(True)


class StoryInsufficient(CustomAction):
    """「小故事解锁」材料不足变体(_story_11 取证):不点任何按钮,停任务。

    每篇解锁消耗的道具相同,一篇不够则篇篇不够,继续读没有意义
    (用户 2026-10-25 定:材料不够也暂停)。只记标记不打日志 ——
    收尾汇总(_main_impl)统一按用户口径输出一行。返回 False →
    on_error: stop 结束任务,游戏画面保持原样。
    """

    def run(self, context, argv: CustomAction.RunArg):
        global _STORY_INSUFFICIENT
        _STORY_INSUFFICIENT = True
        return False


def story_overrides(story_type="member", max_stories=50):
    """按故事类型改写 story.json 的节点(小故事/回忆小故事按钮不同)。

    两个按钮都在成员详情页左下角并排,模板与 roi 成对替换,避免串位。
    """
    if story_type == "memoir":
        btn = "story/story_memoir_btn.png"
        roi = [380, 560, 300, 110]
    else:
        btn = "story/story_member_btn.png"
        roi = [100, 560, 300, 110]
    return {
        "story_member_detail": {"template": btn, "roi": roi},
        "story_click_avatar": {
            "custom_action_param": {"story_type": story_type, "max_stories": max_stories},
        },
        "story_reward_dialog": {
            "custom_action_param": {"story_type": story_type},
        },
    }
