import { Table, Tag } from 'antd';
import { useEffect, useState } from 'react';
import { api, SyncLog } from '../api/client';
export default function Logs() {
  const [rows, setRows] = useState<SyncLog[]>([]); useEffect(() => { api.get('/api/sync/logs').then((r) => setRows(r.data)); }, []);
  return <Table rowKey="id" dataSource={rows} columns={[
    { title:'同步类型', dataIndex:'sync_type' }, { title:'开始时间', dataIndex:'started_at' }, { title:'结束时间', dataIndex:'ended_at' },
    { title:'成功', dataIndex:'success_count' }, { title:'失败', dataIndex:'failed_count' },
    { title:'状态', dataIndex:'status', render:(s:string)=><Tag color={s==='success'?'green':s==='failed'?'red':'orange'}>{s}</Tag> }, { title:'错误原因', dataIndex:'error_message' }
  ]}/>;
}
