import { Button, Modal, Space, Table, Tag, App } from 'antd';
import { useEffect, useState } from 'react';
import { api, SyncState } from '../api/client';

function raw(row: SyncState): any {
  try { return row.raw_json ? JSON.parse(row.raw_json) : {}; } catch { return {}; }
}

export default function Orders() {
  const [rows, setRows] = useState<SyncState[]>([]); const [json, setJson] = useState(''); const { message } = App.useApp();
  const load = () => api.get('/api/orders').then((r) => setRows(r.data));
  useEffect(() => { load(); }, []);
  const resync = async (id:string) => { try { await api.post(`/api/sync/order/${id}`); message.success('已重新同步'); load(); } catch(e:any) { message.error(e.response?.data?.error ?? '同步失败'); } };
  return <>
  <Table rowKey="salesforce_order_id" dataSource={rows} columns={[
    { title:'订单编号', dataIndex:'salesforce_order_number' },
    { title:'Salesforce订单ID', dataIndex:'salesforce_order_id' },
    { title:'订单状态', render:(_, r) => raw(r).Status },
    { title:'客户名称', render:(_, r) => raw(r).Account?.Name },
    { title:'订单金额', render:(_, r) => raw(r).TotalAmount },
    { title:'Salesforce更新时间', dataIndex:'last_salesforce_modified_at' },
    { title:'同步状态', dataIndex:'sync_status', render:(s:string)=><Tag color={s==='success'?'green':s==='failed'?'red':'blue'}>{s}</Tag> },
    { title:'最后同步时间', dataIndex:'last_synced_at' },
    { title:'操作', render:(_, r) => <Space><Button size="small" onClick={() => resync(r.salesforce_order_id)}>重新同步</Button><Button size="small" onClick={() => setJson(r.raw_json ?? '{}')}>查看JSON</Button><a target="_blank" href={`/lightning/r/Order/${r.salesforce_order_id}/view`}>Salesforce</a>{r.bitable_record_id && <Tag>飞书:{r.bitable_record_id}</Tag>}</Space> }
  ]} />
  <Modal title="原始JSON" open={!!json} onCancel={() => setJson('')} footer={null} width={800}><pre>{json}</pre></Modal>
  </>;
}
