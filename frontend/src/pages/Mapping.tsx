import { Button, Card, Form, Input, InputNumber, Table, App } from 'antd';
import { useEffect, useState } from 'react';
import { api, FieldMapping } from '../api/client';
export default function Mapping() {
  const [rows, setRows] = useState<FieldMapping[]>([]); const { message } = App.useApp();
  const load = () => api.get('/api/mapping').then((r) => setRows(r.data)); useEffect(() => { load(); }, []);
  const save = async () => { await api.put('/api/mapping', { items: rows }); message.success('字段映射已保存'); };
  const update = (i:number, patch:Partial<FieldMapping>) => setRows(rows.map((r, idx) => idx === i ? { ...r, ...patch } : r));
  return <Card title="字段映射" extra={<Button type="primary" onClick={save}>保存</Button>}><Table rowKey="salesforce_field" pagination={false} dataSource={rows} columns={[
    { title:'Salesforce字段', dataIndex:'salesforce_field' },
    { title:'飞书字段', dataIndex:'feishu_field', render:(v, _r, i)=><Input value={v} onChange={(e)=>update(i,{feishu_field:e.target.value})}/> },
    { title:'类型', dataIndex:'field_type', render:(v, _r, i)=><Input value={v} onChange={(e)=>update(i,{field_type:e.target.value})}/> },
    { title:'启用', dataIndex:'enabled', render:(v, _r, i)=><InputNumber min={0} max={1} value={v} onChange={(n)=>update(i,{enabled:Number(n)})}/> }
  ]}/></Card>;
}
