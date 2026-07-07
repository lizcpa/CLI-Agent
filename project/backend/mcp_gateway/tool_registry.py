from .models import MCPToolDefinition

TOOL_REGISTRY: list[MCPToolDefinition] = [
    MCPToolDefinition(
        name="crawl_hot_product",
        description="从电商平台采集热门商品数据。支持抖音、淘宝、Amazon、Shopee等平台。按销量/热度排序，返回标准化商品列表。异步任务，返回task_id后通过query_task_status轮询结果。",
        inputSchema={
            "type": "object",
            "properties": {
                "platform": {"type": "string", "description": "采集平台: douyin, taobao, amazon, shopee", "enum": ["douyin", "taobao", "amazon", "shopee"]},
                "keyword": {"type": "string", "description": "搜索关键词"},
                "max_count": {"type": "integer", "description": "最大采集数量，默认100", "default": 100},
                "sort_by": {"type": "string", "description": "排序方式: sales, price, rating", "enum": ["sales", "price", "rating"], "default": "sales"},
            },
            "required": ["platform", "keyword"],
        },
    ),
    MCPToolDefinition(
        name="analyze_product",
        description="对商品进行多维度评分分析（热度40%、转化率35%、利润率25%），给出选品决策建议（hot/normal/cold）。支持批量分析。",
        inputSchema={
            "type": "object",
            "properties": {
                "product_ids": {"type": "array", "items": {"type": "integer"}, "description": "商品ID列表，为空则按平台批量分析"},
                "platform": {"type": "string", "description": "平台过滤"},
                "limit": {"type": "integer", "description": "分析数量上限，默认100", "default": 100},
            },
        },
    ),
    MCPToolDefinition(
        name="generate_copywriting",
        description="基于商品信息生成营销文案。支持多种风格（营销型、种草型、专业型），可指定目标长度和语言模型。",
        inputSchema={
            "type": "object",
            "properties": {
                "product_id": {"type": "integer", "description": "商品ID"},
                "product_title": {"type": "string", "description": "商品标题"},
                "product_desc": {"type": "string", "description": "商品描述"},
                "keywords": {"type": "array", "items": {"type": "string"}, "description": "关键词列表"},
                "style": {"type": "string", "description": "文案风格", "enum": ["marketing", "social", "professional"], "default": "marketing"},
                "max_length": {"type": "integer", "description": "最大字数", "default": 200},
                "model": {"type": "string", "description": "指定LLM模型，留空自动选择"},
            },
            "required": ["product_id", "product_title"],
        },
    ),
    MCPToolDefinition(
        name="generate_images",
        description="根据提示词生成商品展示图片。支持批量生成，可指定尺寸、数量、反向词。异步任务。",
        inputSchema={
            "type": "object",
            "properties": {
                "prompts": {"type": "array", "items": {"type": "string"}, "description": "提示词列表"},
                "size": {"type": "string", "description": "图片尺寸", "enum": ["512x512", "768x768", "1024x1024", "2048x2048"], "default": "1024x1024"},
                "n": {"type": "integer", "description": "每提示词生成数量", "default": 1},
                "negative_prompt": {"type": "string", "description": "反向提示词"},
                "model": {"type": "string", "description": "指定生图模型，留空自动选择"},
                "seed": {"type": "integer", "description": "随机种子"},
            },
            "required": ["prompts"],
        },
    ),
    MCPToolDefinition(
        name="generate_video_clips",
        description="生成商品视频片段。支持文生视频和参考图生视频，可指定时长、分辨率、运动强度。异步任务。",
        inputSchema={
            "type": "object",
            "properties": {
                "type": {"type": "string", "description": "生成类型", "enum": ["text2video", "image2video"], "default": "text2video"},
                "prompts": {"type": "array", "items": {"type": "string"}, "description": "文本提示词"},
                "reference_image_url": {"type": "string", "description": "参考图片URL（image2video时必填）"},
                "duration": {"type": "integer", "description": "视频时长(秒)", "default": 5},
                "resolution": {"type": "string", "description": "分辨率", "enum": ["720p", "1080p"], "default": "1080p"},
                "count": {"type": "integer", "description": "生成数量", "default": 1},
                "motion_strength": {"type": "number", "description": "运动强度 0-1", "default": 0.5},
                "model": {"type": "string", "description": "指定模型，留空自动"},
            },
            "required": ["prompts"],
        },
    ),
    MCPToolDefinition(
        name="compose_video",
        description="将多个视频片段、图片、音频合成为最终视频。支持字幕压制、转场效果、BGM。异步任务，合成耗时较长。",
        inputSchema={
            "type": "object",
            "properties": {
                "pipeline_id": {"type": "string", "description": "流水线ID"},
                "video_clips": {"type": "array", "items": {"type": "string"}, "description": "视频片段URL列表"},
                "images": {"type": "array", "items": {"type": "string"}, "description": "图片URL列表"},
                "audio_url": {"type": "string", "description": "背景音频URL"},
                "subtitle_text": {"type": "string", "description": "字幕文本"},
                "template_id": {"type": "string", "description": "合成模板ID"},
                "config": {"type": "object", "description": "额外FFmpeg配置参数"},
            },
            "required": ["pipeline_id", "video_clips"],
        },
    ),
    MCPToolDefinition(
        name="publish_content",
        description="将视频发布到指定平台。支持多平台同时发布、定时发布。自动适配平台格式要求。",
        inputSchema={
            "type": "object",
            "properties": {
                "pipeline_id": {"type": "string", "description": "流水线ID"},
                "video_url": {"type": "string", "description": "视频URL"},
                "platforms": {"type": "array", "items": {"type": "string"}, "description": "目标平台列表: douyin, taobao, amazon, shopee, youtube, tiktok"},
                "title": {"type": "string", "description": "发布标题"},
                "description": {"type": "string", "description": "发布描述"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "标签列表"},
                "scheduled_time": {"type": "string", "description": "定时发布时间(ISO 8601格式)"},
            },
            "required": ["pipeline_id", "video_url", "platforms", "title"],
        },
    ),
    MCPToolDefinition(
        name="query_task_status",
        description="查询异步任务的执行状态和结果。支持所有异步工具（crawl_hot_product, generate_images, generate_video_clips, compose_video, publish_content）产生的任务。",
        inputSchema={
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "任务ID"},
            },
            "required": ["task_id"],
        },
    ),
    MCPToolDefinition(
        name="list_models",
        description="列出可用的AI模型列表及状态。可按类型过滤(image/video/llm/tts)。",
        inputSchema={
            "type": "object",
            "properties": {
                "type": {"type": "string", "description": "模型类型过滤", "enum": ["image", "video", "llm", "tts"]},
            },
        },
    ),
]
