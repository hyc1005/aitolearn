# 智慧学伴教师工作台

Windows 原生应用。前端使用 React、Ant Design 和 ECharts。后端使用 FastAPI、SQLite FTS5、PaddleOCR 和 smolagents。业务文件和数据库位于本机 `var/`。

## 安装与启动

安装 Python 3.11 x64 和 Node.js 22 或以上（含 npm）。以 PowerShell 从项目根目录运行。

    .\scripts\setup.ps1
    .\scripts\start.ps1

打开 http://127.0.0.1:8765。执行 `scripts/stop.ps1` 停止。遇到问题运行 `scripts/doctor.ps1`，查看 `var/api-error.log` 和 `var/worker-error.log`。处理旧版 DOC 需安装 LibreOffice，可通过 `SOFFICE` 指定可执行文件。首次 OCR 会初始化 Paddle 资源。

可用 `setup.ps1 -Python C:\path\python.exe` 指定解释器。仅验证数字 PDF、表格和 UI 时用 `setup.ps1 -SkipOcr -KeywordOnly`，并以 `start.ps1 -KeywordOnly` 启动。Windows x64 的 Python 3.12 关键词模式使用验收时固定的依赖版本。混合检索需要完整安装和首次向量模型下载。

模型默认走 OpenCode Go 的 `glm-5.3-flash`。通过本机环境设置 `OPENCODE_GO_API_KEY`，也兼容根目录已有的 `goapi.txt`。接口可由 `OPENCODE_GO_BASE_URL` 覆盖。启动时优先使用环境变量。运行 `.\.venv\Scripts\python.exe -m scripts.check_model` 可以用自制算术题验收模型工具调用。

## 首次使用

1. 在**导入与清洗**选择赛题 XLSX 或五张业务 CSV，点击「运行 Agent 清洗」。进入**Agent 工作台**查看 Python 结果与 GLM 检查意见。模型收到汇总数字、异常类型和脱敏问题编号。核对后点击「确认并启用快照」。使用历史验收库时，可在工作台点击「迁移历史批次」，复核通过后确认。
2. 在**课程与资料**点击“整理已导入题库”。检查后发布，等待索引。需要课标或教材时上传相应 PDF，检查解析和原文位置后发布。
3. 打开**学生画像**，选择 D01 与 N01。点击整式相关指标查看 Q015 的步骤、填写和评分点。可从该条作答直接点「基于本题生成建议」。工作台会记录学情解读、草稿生成和独立模型终审，可通过运行链接回到同一任务。
4. 打开草稿，在各条教学说明下查看对应原文，在练习下展开对应作答。修订说明、练习提示、顺序或给学生的话后，点击「保存修订并重新终审」。当前版本通过终审后，点击「确认并预览卡片」。
5. 页顶 JSON 和 CSV 用于诊断。**学习建议**页的「导出已确认成果」仅纳入属于当前快照、终审通过且由教师确认的建议，并附带数据与资料版本清单。

各项得分率只采用通过关联及分数校验的已评分记录。学生轴按有效得分除以满分统计，班级轴是个人得分率均值。画像和运维可在模型不可用时继续使用。学生 ID、教材和作答由教师提供的批次决定，建议由运行时模型生成。

## 本机验证

    .\.venv\Scripts\python.exe -m unittest discover -s tests -v
    npm.cmd --prefix frontend run build

离线复跑默认 Python 清洗工具，可传入单个 XLSX、单个 CSV 或 CSV 目录。产物包含业务表、报告、异常、全部原始行位置及版本清单。

    .\.venv\Scripts\python.exe -m scripts.clean_data --input path\to\workbook.xlsx --output var\cleaning-check

公开赛题基准为 806 行原始作答、799 个事件、790 个已评分状态、785 条有效已评分、5 条异常。

开发前端热更新使用 `npm.cmd --prefix frontend run dev`，API 由 Vite 代理。OpenAPI 位于 `/docs`。
