"""本地开发样题导入：自编内容，仅新增，不覆盖已发布版本。"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import create_db_engine
from app.models import Domain, Question, QuestionVersion, Track

SAMPLES = {
    "computer": (
        "computer-basics",
        "计算机基础",
        [
            (
                "栈通常遵循哪一种数据访问顺序？",
                ["先进先出", "后进先出", "随机访问", "按值排序"],
                "B",
                "栈从同一端入栈和出栈，因此最后入栈的元素最先出栈。",
            ),
            (
                "在关系数据库中，主键的主要作用是什么？",
                ["唯一标识一行", "压缩整张表", "自动加密", "记录修改时间"],
                "A",
                "主键唯一标识表中的每一行，主键值不能为 NULL。",
            ),
            (
                "二分查找通常要求待查找序列具备什么条件？",
                ["元素必须为整数", "长度必须为偶数", "按比较规则有序", "所有值相同"],
                "C",
                "二分查找利用有序性，根据比较结果排除一半候选范围。",
            ),
            (
                "HTTP 状态码 404 通常表示什么？",
                ["请求成功", "永久重定向", "服务器内部错误", "找不到请求的资源"],
                "D",
                "404 表示服务器未找到请求的资源；它不同于表示服务器内部错误的 500。",
            ),
            (
                "数据库事务的原子性意味着什么？",
                [
                    "操作全部成功或全部回滚",
                    "每条 SQL 都独立提交",
                    "不需要处理并发",
                    "数据只能读取一次",
                ],
                "A",
                "原子性将一组操作视作整体：全部完成，或在失败时撤销，避免留下部分结果。",
            ),
            (
                "队列通常遵循哪一种顺序？",
                ["后进先出", "按优先级固定排序", "先进先出", "只允许随机删除"],
                "C",
                "普通队列从队尾加入、队首移除，先进入的元素通常先离开。",
            ),
        ],
    ),
    "english": (
        "english-vocabulary",
        "英语词汇",
        [
            (
                "Please save the file before closing the window. 其中 save 的意思是？",
                ["删除", "保存", "移动", "关闭"],
                "B",
                "这里 save the file 表示保存文件，before closing 表示关闭之前。",
            ),
            (
                "The instructions are clear. 其中 clear 最接近哪个意思？",
                ["清晰易懂的", "昂贵的", "迟到的", "相反的"],
                "A",
                "clear 在描述 instructions 时，表示说明清楚、容易理解。",
            ),
            (
                "Choose the correct answer. 其中 choose 的意思是？",
                ["复制", "忘记", "选择", "隐藏"],
                "C",
                "choose 表示选择；correct answer 表示正确答案。",
            ),
            (
                "We need to improve the process. 其中 improve 的意思是？",
                ["取消", "重复", "忽略", "改进"],
                "D",
                "improve 表示使事物变得更好，这里指改进流程。",
            ),
            (
                "This task requires patience. 其中 patience 的意思是？",
                ["耐心", "速度", "许可", "设备"],
                "A",
                "patience 是耐心；requires 表示需要。",
            ),
            (
                "Please review your answers. 其中 review 的意思是？",
                ["打印", "隐藏", "复查", "替换"],
                "C",
                "这里 review 表示重新查看、检查自己的答案。",
            ),
        ],
    ),
}


def seed_samples(engine):
    with Session(engine) as session, session.begin():
        for domain_code, (track_code, track_name, samples) in SAMPLES.items():
            domain = session.scalar(select(Domain).where(Domain.code == domain_code))
            if domain is None:
                raise ValueError("请先执行数据库迁移，创建知识领域")
            track = session.scalar(select(Track).where(Track.code == track_code))
            if track is None:
                track = Track(domain_id=domain.id, code=track_code, name=track_name)
                session.add(track)
                session.flush()
            for number, (stem, choices, answer, explanation) in enumerate(samples, 1):
                code = f"sample-{track_code}-{number:02}"
                question = session.scalar(select(Question).where(Question.code == code))
                if question is None:
                    question = Question(code=code, track_id=track.id)
                    session.add(question)
                    session.flush()
                content = {
                    "stem": stem,
                    "options": [
                        {"id": key, "text": value}
                        for key, value in zip("ABCD", choices, strict=True)
                    ],
                    "correct_answer": answer,
                    "explanation": explanation,
                    "source": "本项目自编开发样题 v1；仅用于功能验收，不代表正式题库质量。",
                }
                version = session.scalar(
                    select(QuestionVersion).where(
                        QuestionVersion.question_id == question.id,
                        QuestionVersion.version == 1,
                    )
                )
                if version:
                    if any(getattr(version, key) != value for key, value in content.items()):
                        raise ValueError(f"已发布样题 {code} 与导入内容不同，拒绝覆盖")
                else:
                    session.add(
                        QuestionVersion(
                            question_id=question.id,
                            version=1,
                            published_at=datetime(2026, 1, 1),
                            **content,
                        )
                    )


if __name__ == "__main__":
    engine = create_db_engine(Settings().database_url.get_secret_value())
    try:
        seed_samples(engine)
        print("已校验并导入 2 个学习方向、12 道自编样题；已有版本未被覆盖。")
    finally:
        engine.dispose()
