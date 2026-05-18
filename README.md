# Salesforce 订单到飞书多维表格单向同步系统

一个 Node.js + TypeScript + Express 后端、React + Vite + Ant Design 前端、SQLite 本地状态库的 MVP，用于将 Salesforce 标准 `Order` 对象单向同步到飞书多维表格。

## 功能

- Salesforce OAuth 2.0 JWT Bearer Flow，支持 Refresh Token Flow 作为可选配置。
- 默认同步 Salesforce 标准 `Order` 主表字段，并支持 SOQL `queryMore` 分页。
- 基于 `LastModifiedDate` 增量同步，增量窗口默认向前回退 5 分钟。
- 飞书自建应用 `tenant_access_token` 自动获取与缓存。
- 支持飞书多维表格新增、更新、批量更新服务封装。
- 以 Salesforce `Order.Id` 作为唯一键，避免重复创建飞书记录。
- 使用 SHA-256 hash 判断订单字段是否变化，无变化则跳过飞书更新。
- SQLite 表：`order_sync_state`、`sync_log`、`field_mapping`、`app_config`。
- 手动全量同步、手动增量同步、单订单重新同步、错误重试、定时增量同步。
- 前端包含 Dashboard、订单列表、字段映射、同步日志、系统配置页面。

## 快速开始

```bash
cp .env.example .env
npm install
npm run dev
```

后端默认监听 `http://localhost:3000`，前端默认监听 `http://localhost:5173`。

## Docker 部署

```bash
cp .env.example .env
# 编辑 .env，并将 Salesforce 私钥放到 ./certs（如使用 JWT Bearer Flow）
docker compose up --build
```

## API

- `GET /health`
- `GET /api/status`
- `POST /api/test/salesforce`
- `POST /api/test/feishu`
- `POST /api/sync/full`
- `POST /api/sync/incremental`
- `POST /api/sync/order/:salesforceOrderId`
- `POST /api/sync/retry`
- `GET /api/orders`
- `GET /api/sync/logs`
- `GET /api/mapping`
- `PUT /api/mapping`
- `GET /api/config`
- `PUT /api/config`

## 环境变量

参考 `.env.example`。核心配置包括 Salesforce Connected App / External Client App 参数、飞书自建应用参数、多维表格 app token/table id、SQLite 文件路径和 cron 表达式。
