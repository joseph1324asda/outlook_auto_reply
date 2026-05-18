import { Button, Card, Space, Statistic, App } from 'antd';
import { useEffect, useState } from 'react';
import { api } from '../api/client';

type Status = { total:number; failed:number; successToday:number; skippedToday:number; recentSyncAt?:string };
export default function Dashboard() {
  const [status, setStatus] = useState<Status>({ total: 0, failed: 0, successToday: 0, skippedToday: 0 });
  const { message } = App.useApp();
  const load = () => api.get('/api/status').then((r) => setStatus(r.data));
  useEffect(() => { load(); }, []);
  const run = async (url: string, label: string) => { const hide = message.loading(`${label}执行中...`, 0); try { await api.post(url); message.success(`${label}完成`); load(); } catch (e:any) { message.error(e.response?.data?.error ?? `${label}失败`); } finally { hide(); } };
  const test = async () => { await Promise.allSettled([api.post('/api/test/salesforce'), api.post('/api/test/feishu')]).then((r) => message.info(`Salesforce: ${r[0].status}; 飞书: ${r[1].status}`)); };
  return <>
    <div className="card-grid">
      <Card><Statistic title="订单总数" value={status.total ?? 0} /></Card>
      <Card><Statistic title="今日新增/更新" value={status.successToday ?? 0} /></Card>
      <Card><Statistic title="今日跳过" value={status.skippedToday ?? 0} /></Card>
      <Card><Statistic title="失败数量" value={status.failed ?? 0} valueStyle={{ color: status.failed ? '#cf1322' : undefined }} /></Card>
    </div>
    <Card title="同步控制" extra={`最近同步：${status.recentSyncAt ?? '暂无'}`}>
      <Space wrap>
        <Button type="primary" onClick={() => run('/api/sync/full', '全量同步')}>全量同步</Button>
        <Button onClick={() => run('/api/sync/incremental', '增量同步')}>增量同步</Button>
        <Button onClick={() => run('/api/sync/retry', '错误重试')}>错误重试</Button>
        <Button onClick={test}>测试连接</Button>
      </Space>
    </Card>
  </>;
}
