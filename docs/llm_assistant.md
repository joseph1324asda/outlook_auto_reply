# LLM 邮件助手设计

该项目提供一个可扩展的脚手架，用于通过 LLM API 生成商业或工程邮件回复，并基于知识库提供可溯源的参考。

## 目录结构
- `llm_mailer/`：主要代码，包括风格预设、知识库加载、提示词拼装和提供方适配。
- `data/`：默认的说明书（manuals）和案例（cases）样例数据（JSON）。
- `requirements.txt`：Python 依赖。

## 功能概述
1. **风格预设**：`styles.py` 定义了三种常用中文邮件风格（简洁商务、工程细节、友好客户成功），包含语气、结构和提醒。
2. **知识库检索**：`knowledge_base.py` 可从 JSON 文件加载说明书和案例条目，并基于关键词计数的简单策略返回最相关的参考条目。
3. **提示词构建**：`prompt_builder.py` 将邮件摘要、客户优先级、附件清单和检索到的参考信息组合成结构化的提示词。
4. **模型提供方抽象**：`ai_client.py` 提供 `AiProvider` 协议，默认使用本地模板生成示例回复，后续可接入真实 API。
5. **命令行体验**：`cli.py` 提供快速生成邮件草稿的入口，便于验证提示词和检索效果。
6. **前端页面**：`web_app.py` 提供表单式交互界面，可生成草稿、上传 PDF 到说明书库，并在页面内配置 API 链接/模型、管理风格预设。

## 使用方法
1. 安装依赖：
   ```bash
   pip install -r requirements.txt
   ```
2. 运行示例：
   ```bash
   python -m llm_mailer.cli "客户反馈接口限流，希望提升到每分钟 120 次" --style concise_business --priority P1
   ```
3. 一键启动前端页面：
   ```bash
   export FLASK_ENV=production  # 可选
   python -m llm_mailer         # 或 python -m llm_mailer.web_app，默认监听 8000 端口
   ```
   - 打开浏览器访问 `http://localhost:8000`，填写邮件摘要并可多选/组合风格预设，一键生成草稿。
   - 页面内可直接输入/选择 API Base URL、模型名、API Key（保存在 `data/app_settings.json`）。
   - 在同一页面选择 PDF 文件即可上传并自动写入 `data/manuals.json`，源文件保存在 `data/manual_uploads/`。
   - 在“创建 / 修改风格预设”区域可新增或覆盖预设，保存到 `data/presets.json`，随后即可在风格列表多选组合。
3. 接入真实模型：
   - 在环境变量中设置 `AI_API_KEY`、可选的 `AI_BASE_URL` 与 `AI_MODEL`。
   - 在 `ai_client.py` 中实现真实的提供方，例如 OpenAI / Azure / 自建推理服务，替换 `get_provider` 返回值。

## 知识库扩展
- 在 `data/manuals.json` 与 `data/cases.json` 中添加新条目，字段包括 `id`、`title`、`kind` 与 `content`。可通过前端上传 PDF 自动追加说明书条目（附 `source` 字段指向原文件位置）。
- 如果需要其他文件格式，可在 `KnowledgeBase.from_json_files` 中扩展解析逻辑。

## 后续迭代建议
- 使用向量检索替换关键词计数，提高召回与排序质量。
- 增加邮件模板多语言支持，并允许自定义签名 / 品牌用语。
- 集成单元测试与 CI，确保提示词构建和检索输出的稳定性。
