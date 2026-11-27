"""Create a wholly synthetic classroom dataset and original teaching handout."""
import csv
from pathlib import Path

from docx import Document


def write_table(root, name, headers, rows):
    with (root / (name + ".csv")).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def main():
    root = Path("var/teacher-demo/input")
    root.mkdir(parents=True, exist_ok=True)
    students = [{"学生编号": f"S{i:02}", "模拟班级编号": "七年一班" if i <= 10 else "七年二班"}
                for i in range(1, 21)]
    topics = [("I1", "T1", "合并同类项"), ("I2", "T2", "解一元一次方程"), ("I3", "T3", "解一元一次不等式")]
    questions = [
        ("Q11", "T1", "I1", "化简 2x+3x。", "5x"),
        ("Q12", "T2", "I2", "解方程 x+3=7。", "x=4"),
        ("Q13", "T3", "I3", "解不等式 x-2>1。", "x>3"),
        ("Q21", "T1", "I1", "化简 4x-x。", "3x"),
        ("Q22", "T2", "I2", "解方程 2x=8。", "x=4"),
        ("Q23", "T3", "I3", "解不等式 -x>2。", "x<-2"),
    ]
    responses = {
        "Q11": ["2x+3x=5x²", "2+3=5", "2x+3x=(2+3)x", "2x+3x=5x，未写同类项说明", "2x+3x=5x"],
        "Q12": ["x=7+3=10", "x+3=7", "x=7-3", "x=4，未代回检验", "x=4，代回 4+3=7"],
        "Q13": ["x>1-2", "x-2>1", "x>1+2", "x>3，未写变形过程", "x>3，两边加 2"],
        "Q21": ["4x-x=3x²", "4-1=3", "4x-x=(4-1)x", "4x-x=3x，未写同类项说明", "4x-x=3x"],
        "Q22": ["x=8-2=6", "2x=8", "x=8÷2", "x=4，未代回检验", "x=4，代回 2×4=8"],
        "Q23": ["x> -2", "-x>2", "两边除以 -1", "x<-2，未写变号原因", "x<-2，除以负数时不等号反向"],
    }
    write_table(root, "模拟学生", ["学生编号", "模拟班级编号"], students)
    write_table(root, "课程要求", ["指标编号", "主题编号", "观察指标"],
                [dict(zip(["指标编号", "主题编号", "观察指标"], item)) for item in topics])
    write_table(root, "测验节点", ["节点编号", "可用题目编号", "模拟测验日期"], [
        {"节点编号": "N01", "可用题目编号": "Q11,Q12,Q13", "模拟测验日期": "2026-10-12"},
        {"节点编号": "N02", "可用题目编号": "Q21,Q22,Q23", "模拟测验日期": "2026-11-09"},
    ])
    write_table(root, "题目与材料", ["材料编号", "材料类型", "主题编号", "题型", "指标编号", "满分", "题干或内容", "参考答案", "参考解析"], [
        {"材料编号": q, "材料类型": "测验题", "主题编号": topic, "题型": "解答题", "指标编号": indicator,
         "满分": 4, "题干或内容": question, "参考答案": answer, "参考解析": "先写出运算步骤，再将结果代回检验。"}
        for q, topic, indicator, question, answer in questions
    ])
    attempts = []
    for i in range(1, 21):
        for node_index in (1, 2):
            for q_index, (_, topic, _, _, answer) in enumerate(questions[(node_index-1)*3:node_index*3], 1):
                event = f"E{i:02}{node_index}{q_index}"
                state = "未作答" if (i, node_index, q_index) == (3, 1, 3) else "待评分" if (i, node_index, q_index) == (7, 2, 2) else "已评分"
                score = min(4, max(0, ((i + q_index) % 5) + (1 if node_index == 2 and i <= 10 else 0)))
                question_id = f"Q{node_index}{q_index}"
                attempts.append({"记录编号": event, "学生编号": f"S{i:02}", "材料编号": question_id,
                    "节点编号": f"N0{node_index}", "作答事件编号": event, "修订版本": 1, "评分状态": state,
                    "得分": score if state == "已评分" else "", "满分": 4, "主题编号": topic,
                    "学生最终作答": responses[question_id][score] if state != "未作答" else ""})
    write_table(root, "作答记录", ["记录编号", "学生编号", "材料编号", "节点编号", "作答事件编号", "修订版本", "评分状态",
                                  "得分", "满分", "主题编号", "学生最终作答"], attempts)
    doc = Document()
    doc.add_heading("七年级数学复习讲义（合成示例）", 0)
    doc.add_paragraph("本讲义及配套学情数据均为人工构造，仅用于功能验收。")
    doc.add_heading("合并同类项", level=1)
    doc.add_paragraph("同类项的字母和各字母指数相同。合并时只把系数相加，字母部分保持不变。例如 2x+3x=5x。")
    doc.add_heading("一元一次方程", level=1)
    doc.add_paragraph("等式两边同时进行相同的运算，等式仍成立。例如 x+3=7，两边都减去 3，得到 x=4。")
    doc.add_heading("一元一次不等式", level=1)
    doc.add_paragraph("不等式两边同时乘或除以负数时，不等号方向要改变。例如 -x>2，两边除以 -1，得到 x<-2。")
    doc.save(root / "原创数学复习讲义.docx")
    print(f"已生成合成数据：{root.resolve()}；20 名学生、2 个班级、2 次测验、120 条作答。")


if __name__ == "__main__":
    main()
