Part of #3

## Question

**前提已变**（#6 决议：开发测试期全云端，本地 GPU 延后）。原题「GPU 节点 = Windows 3070」作废，改为**云端 VLM/OCR API 选型**。要定：

1. **图片概述 + 视频抽帧概述的云端 VLM API**：候选（国内直连、已测可达）：
   - SiliconFlow `Qwen/Qwen2.5-VL-*`（OpenAI 兼容，图文）
   - 阿里云 DashScope `qwen-vl-plus` / `qwen-vl-max`
   - 智谱 GLM-4V
   哪个在「中文文档截图 / 流程图 / 扫描件页」概述上性价比最高？单价多少？

2. **OCR 选型（云端）**：扫描版 PDF 的 OCR 走哪条——
   - SiliconFlow 上 `PaddleOCR-VL-1.5` **免费**
   - 或本机 RapidOCR（CPU，已实测中文最好）在开发测试期是否还保留（作为本地兜底，不花钱）？
   
   ⚠️ 注意：WeKnora 的 OCR 在 Go 侧、不配 VLM 则扫描件无文本进索引——云端 WeKnora 也要配一个 VLM 才能 OCR，这与本系统自己的 OCR 是不是同一路？

3. **视频抽帧策略**：抽帧间隔（按秒 / 按场景变化检测）、单视频抽帧上限、每帧概述后如何汇总成一条视频记录。

推荐倾向：概述用 **SiliconFlow Qwen2.5-VL**（OpenAI 兼容、国内直连、按 token 计费便宜）；OCR 主力 **SiliconFlow PaddleOCR-VL-1.5（免费）**，本机 RapidOCR 作离线兜底；视频按固定间隔 + 变化检测去冗余。

相关事实见 `docs/research/embedding-model-evaluation.md`（§中国可达集合、§OCR）与 `docs/research/cloud-deployment-options.md`。
