import { escapeHtml } from '../utils.js';
import { openDialog } from './task-dialog.js?v=20261126-zai-1';

// 全站通用对话框（2026-11-26）：替代浏览器原生 window.confirm / prompt / alert。
// 外壳与任务操作对话框共用（#taskOpDialog，weave.css R16 .atl-* + R18 .tod-*），挂 body、z 13000，
// 从任何业务弹窗里调用都在最上层；Esc / 点遮罩 = 取消，回车 = 确定（多行输入框里回车换行）。
//
//   await confirmDialog({ title, message, danger })            → true / false
//   await promptDialog({ title, label, value, type, required }) → 字符串 / null（取消）
//   await formDialog({ title, fields: [{ name, label, … }] })  → { name: value } / null
//   await alertDialog({ title, message })                      → 关闭后 resolve
// 都接受只传一个字符串（当作 message）。message 里的换行会保留。
// 普通脚本（learning.js、index.html 内联脚本）用 window.OmniQADialog.confirm / prompt / form / alert。

const norm = (opts) => (typeof opts === 'string' ? { message: opts } : (opts || {}));
const subHtml = (sub) => (sub ? `<span class="atl-target">${escapeHtml(sub)}</span>` : '');

function toneOf(o) {
  if (o.tone) return o.tone;
  return o.danger ? 'red' : 'accent';
}

function iconOf(o, fallback) {
  if (o.icon) return o.icon;
  return o.danger ? 'i-trash' : fallback;
}

export async function confirmDialog(opts) {
  const o = norm(opts);
  const res = await openDialog({
    icon: iconOf(o, 'i-alert'),
    tone: toneOf(o),
    title: o.title || (o.danger ? '确认删除' : '请确认'),
    sub: subHtml(o.sub),
    message: escapeHtml(o.message || ''),
    okText: o.okText || (o.danger ? '删除' : '确定'),
    cancelText: o.cancelText || '取消',
    danger: !!o.danger,
  });
  return !!res;
}

export async function alertDialog(opts) {
  const o = norm(opts);
  await openDialog({
    icon: o.icon || 'i-info',
    tone: o.tone || 'accent',
    title: o.title || '提示',
    sub: subHtml(o.sub),
    message: escapeHtml(o.message || ''),
    okText: o.okText || '知道了',
    alertOnly: true,
  });
}

// 单项输入。type: text | password | textarea；required=false 时允许留空（返回 ''）
export async function promptDialog(opts) {
  const o = norm(opts);
  const values = await formDialog({
    ...o,
    icon: o.icon || (o.type === 'password' ? 'i-key' : 'i-edit'),
    fields: [{
      name: 'value',
      label: o.label || '内容',
      type: o.type,
      value: o.value ?? '',
      placeholder: o.placeholder,
      hint: o.hint,
      prefix: o.prefix,
      required: o.required,
      maxlength: o.maxlength,
      rows: o.rows,
      validate: o.validate,
    }],
  });
  return values ? values.value : null;
}

export async function formDialog(opts) {
  const o = norm(opts);
  const fields = Array.isArray(o.fields) ? o.fields : [];
  const res = await openDialog({
    icon: iconOf(o, 'i-edit'),
    tone: toneOf(o),
    title: o.title || '请填写',
    sub: subHtml(o.sub),
    message: escapeHtml(o.message || ''),
    fields,
    okText: o.okText || '确定',
    cancelText: o.cancelText || '取消',
    danger: !!o.danger,
    wide: o.wide ?? fields.some((f) => f.type === 'textarea'),
  });
  return res ? res.values : null;
}

window.OmniQADialog = { confirm: confirmDialog, prompt: promptDialog, form: formDialog, alert: alertDialog };
