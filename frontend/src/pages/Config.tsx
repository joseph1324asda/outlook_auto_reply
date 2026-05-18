import { Button, Card, Form, Input, App } from 'antd';
import { useEffect } from 'react';
import { api } from '../api/client';
const KEYS = ['SALESFORCE_CLIENT_ID','SALESFORCE_USERNAME','SALESFORCE_PRIVATE_KEY_PATH','SALESFORCE_LOGIN_URL','SALESFORCE_API_VERSION','SALESFORCE_REFRESH_TOKEN','SALESFORCE_CLIENT_SECRET','FEISHU_APP_ID','FEISHU_APP_SECRET','FEISHU_BITABLE_APP_TOKEN','FEISHU_BITABLE_TABLE_ID'];
export default function Config() {
  const [form] = Form.useForm(); const { message } = App.useApp();
  useEffect(() => { api.get('/api/config').then((r) => form.setFieldsValue(Object.fromEntries(r.data.map((x:any) => [x.key, x.value])))); }, [form]);
  const save = async (values:Record<string,string>) => { await api.put('/api/config', { items: Object.entries(values).map(([key, value]) => ({ key, value: value ?? '', secret: key.includes('SECRET') || key.includes('TOKEN') })) }); message.success('配置已保存'); };
  return <Card title="系统配置"><Form form={form} layout="vertical" onFinish={save}>{KEYS.map((key) => <Form.Item key={key} name={key} label={key}><Input.Password visibilityToggle={key.includes('SECRET') || key.includes('TOKEN')} /></Form.Item>)}<Button type="primary" htmlType="submit">保存</Button></Form></Card>;
}
