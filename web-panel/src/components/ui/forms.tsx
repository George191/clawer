import React, { Children, cloneElement, createContext, forwardRef, isValidElement, useContext, useEffect, useId, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react';
import { createPortal } from 'react-dom';
import dayjs, { type Dayjs } from 'dayjs';
import customParseFormat from 'dayjs/plugin/customParseFormat';
import './forms.css';
dayjs.extend(customParseFormat);

type Values = Record<string, any>;
type NamePath = string | number | Array<string | number>;
type Rule = { required?: boolean; whitespace?: boolean; type?: 'email' | 'number' | 'integer' | 'string' | 'array'; min?: number; max?: number; len?: number; pattern?: RegExp; message?: React.ReactNode; validator?: (rule: Rule, value: any) => void | Promise<void> };
type RuleEntry = Rule | ((form: FormInstance) => Rule);
type Field = { path: NamePath; id: string; label?: React.ReactNode; rules?: RuleEntry[]; dependencies?: NamePath[] };
const parts = (name: NamePath) => Array.isArray(name) ? name : [name];
const keyOf = (name: NamePath) => JSON.stringify(parts(name));
const read = (values: Values, name: NamePath): any => parts(name).reduce((value: any, key) => value?.[key], values);
function assign(values: Values, name: NamePath, value: any): Values {
  const result = { ...values }; let target: any = result, current: any = values;
  parts(name).forEach((key, index, path) => {
    if (index === path.length - 1) target[key] = value;
    else { target[key] = Array.isArray(current?.[key]) ? [...current[key]] : { ...current?.[key] }; current = current?.[key]; target = target[key]; }
  });
  return result;
}
const text = (node: React.ReactNode): string => typeof node === 'string' || typeof node === 'number' ? String(node) : Array.isArray(node) ? node.map(text).join('') : isValidElement(node) ? text((node.props as { children?: React.ReactNode }).children) : '';
export interface FormInstance<T extends Values = Values> {
  getFieldValue(name: NamePath): any;
  getFieldsValue(all?: boolean | NamePath[]): T;
  setFieldsValue(values: Partial<T>): void;
  setFieldValue(name: NamePath, value: any): void;
  resetFields(names?: NamePath[]): void;
  validateFields(names?: NamePath[]): Promise<T>;
  getFieldError(name: NamePath): React.ReactNode[];
  getFieldsError(): Array<{ name: Array<string | number>; errors: React.ReactNode[] }>;
  submit(): Promise<void>;
  _subscribe(listener: () => void): () => void;
  _snapshot(): number;
  _fields: Map<string, Field>;
  _initial: Values;
  _initialized: boolean;
  _initialize(values: Values): void;
  _submit?: () => Promise<void>;
  _onValuesChange?: (changed: Values, values: any) => void;
}
function createForm<T extends Values>(): FormInstance<T> {
  let values: Values = {}, version = 0;
  const errors = new Map<string, React.ReactNode[]>(), listeners = new Set<() => void>();
  const emit = () => { version++; listeners.forEach(listener => listener()); };
  const form: FormInstance<T> = {
    _fields: new Map(), _initial: {}, _initialized: false,
    _initialize(initial) { if (!form._initialized) { form._initial = { ...initial }; values = { ...initial, ...values }; form._initialized = true; } },
    _subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    _snapshot: () => version,
    getFieldValue: name => read(values, name),
    getFieldsValue(names) { return (Array.isArray(names) ? names.reduce((result, name) => assign(result, name, read(values, name)), {}) : { ...values }) as T; },
    setFieldsValue(next) { values = { ...values, ...next }; Object.keys(next).forEach(name => errors.delete(keyOf(name))); emit(); },
    setFieldValue(name, value) {
      values = assign(values, name, value); errors.delete(keyOf(name)); emit();
      const dependent = [...form._fields.values()].filter(field => field.dependencies?.some(path => keyOf(path) === keyOf(name))).map(field => field.path);
      if (dependent.length) void form.validateFields(dependent).catch(() => {});
    },
    resetFields(names) { if (names) names.forEach(name => { values = assign(values, name, read(form._initial, name)); errors.delete(keyOf(name)); }); else { values = { ...form._initial }; errors.clear(); } emit(); },
    getFieldError: name => errors.get(keyOf(name)) || [],
    getFieldsError: () => [...form._fields.values()].map(field => ({ name: parts(field.path), errors: form.getFieldError(field.path) })),
    async validateFields(names) {
      const snapshot = values;
      const fields = [...form._fields.values()].filter(field => !names || names.some(name => keyOf(name) === keyOf(field.path)));
      const failed: Array<{ name: Array<string | number>; errors: React.ReactNode[] }> = [];
      await Promise.all(fields.map(async field => {
        const value = read(snapshot, field.path), messages: React.ReactNode[] = [];
        const empty = value == null || value === '' || Array.isArray(value) && !value.length;
        for (const entry of field.rules || []) {
          const rule = typeof entry === 'function' ? entry(form) : entry;
          let reason: string | undefined;
          if (rule.required && (empty || rule.whitespace && typeof value === 'string' && !value.trim())) reason = `${text(field.label) || parts(field.path).join('.')} is required.`;
          if (!empty) {
            if (rule.type === 'email' && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(value))) reason = 'Enter a valid email address.';
            if (rule.type === 'integer' && !Number.isInteger(value)) reason = 'Enter a whole number.';
            if (rule.type === 'number' && (typeof value !== 'number' || !Number.isFinite(value))) reason = 'Enter a valid number.';
            if (rule.type === 'array' && !Array.isArray(value)) reason = 'Choose valid values.';
            if (rule.type === 'string' && typeof value !== 'string') reason = 'Enter text.';
            const length = typeof value === 'number' ? value : String(value).length;
            if (rule.min !== undefined && length < rule.min) reason = typeof value === 'number' ? `Minimum value is ${rule.min}.` : `Use at least ${rule.min} characters.`;
            if (rule.max !== undefined && length > rule.max) reason = typeof value === 'number' ? `Maximum value is ${rule.max}.` : `Use at most ${rule.max} characters.`;
            if (rule.len !== undefined && length !== rule.len) reason = `Use exactly ${rule.len} characters.`;
            if (rule.pattern) { rule.pattern.lastIndex = 0; if (!rule.pattern.test(String(value))) reason = 'Enter a valid value.'; }
          }
          if (reason) messages.push(rule.message || reason);
          if (rule.validator) { try { await rule.validator(rule, value); } catch (error) { messages.push(rule.message || (error instanceof Error ? error.message : String(error))); } }
        }
        if (messages.length) failed.push({ name: parts(field.path), errors: messages });
        if (values === snapshot) errors.set(keyOf(field.path), messages);
      }));
      if (values !== snapshot) throw { values: { ...values }, errorFields: [], outOfDate: true };
      emit();
      if (failed.length) throw { values: { ...values }, errorFields: failed, outOfDate: false };
      return { ...values } as T;
    },
    submit() { return form._submit ? form._submit() : form.validateFields().then(() => {}); },
  };
  return form;
}
const FormContext = createContext<{ form: FormInstance; disabled?: boolean; preserve?: boolean; name: string } | null>(null);
function useForm<T extends Values = Values>(provided?: FormInstance<T>): [FormInstance<T>] { const ref = useRef<FormInstance<T>>(); if (!ref.current) ref.current = provided || createForm<T>(); return [provided || ref.current]; }
function useWatch(name: NamePath, supplied?: FormInstance) { const context = useContext(FormContext), form = supplied || context?.form; useSyncExternalStore(form?._subscribe || (() => () => {}), form?._snapshot || (() => 0)); return form?.getFieldValue(name); }
export interface FormProps<T extends Values = Values> extends Omit<React.FormHTMLAttributes<HTMLFormElement>, 'onFinish' | 'onChange' | 'onSubmit'> { form?: FormInstance<any>; initialValues?: Values; layout?: 'vertical' | 'horizontal' | 'inline'; disabled?: boolean; preserve?: boolean; onFinish?: (values: any) => any; onFinishFailed?: (error: any) => void; onValuesChange?: (changed: Values, values: T) => void; }
function FormRoot<T extends Values = Values>({ form: supplied, initialValues = {}, layout = 'vertical', disabled, preserve = true, onFinish, onFinishFailed, onValuesChange, children, className = '', name, onReset, ...rest }: FormProps<T>) {
  const [form] = useForm(supplied), generated = useId(), pending = useRef<Promise<void> | null>(null);
  form._initialize(initialValues); form._onValuesChange = onValuesChange;
  const submit = () => {
    if (pending.current) return pending.current;
    pending.current = (async () => {
      // Native password managers can update a control without dispatching React's change event.
      const autofilled: Array<{ path: NamePath; value: string }> = [];
      for (const field of form._fields.values()) {
        const input = document.getElementById(field.id);
        if ((input instanceof HTMLInputElement || input instanceof HTMLTextAreaElement) && !['combobox', 'spinbutton'].includes(input.getAttribute('role') || '') && !(input instanceof HTMLInputElement && ['checkbox', 'radio', 'file'].includes(input.type)) && input.value !== form.getFieldValue(field.path)) autofilled.push({ path: field.path, value: input.value });
      }
      // Snapshot every control before a store update rerenders the remaining controls.
      autofilled.forEach(({ path, value }) => form.setFieldValue(path, value));
      let values: T;
      try { values = await form.validateFields(); }
      catch (error) { onFinishFailed?.(error); const field = (error as { errorFields?: Array<{ name: NamePath }> }).errorFields?.[0]; if (field) document.getElementById(form._fields.get(keyOf(field.name))?.id || '')?.focus(); return; }
      await onFinish?.(values);
    })().finally(() => { pending.current = null; });
    return pending.current;
  };
  form._submit = submit;
  return <FormContext.Provider value={{ form, disabled, preserve, name: name || `form-${generated}` }}><form {...rest} name={name} noValidate className={`ui-form ui-form-${layout} ${className}`} onSubmit={event => { event.preventDefault(); if (!disabled) void submit(); }} onReset={event => { form.resetFields(); onReset?.(event); }}>{children}</form></FormContext.Provider>;
}
export interface FormItemProps { name?: NamePath; label?: React.ReactNode; rules?: RuleEntry[]; children?: React.ReactNode | ((form: FormInstance) => React.ReactNode); valuePropName?: string; noStyle?: boolean; shouldUpdate?: boolean | ((previous: Values, current: Values) => boolean); dependencies?: NamePath[]; preserve?: boolean; required?: boolean; htmlFor?: string; extra?: React.ReactNode; help?: React.ReactNode; validateStatus?: string; className?: string; style?: React.CSSProperties; initialValue?: any; getValueFromEvent?: (...args: any[]) => any; }
function FormItem({ name, label, rules, children, valuePropName = 'value', noStyle, dependencies, preserve, required, htmlFor, extra, help, validateStatus, className = '', style, initialValue, getValueFromEvent }: FormItemProps) {
  const context = useContext(FormContext), generated = useId();
  const form = context?.form;
  useSyncExternalStore(form?._subscribe || (() => () => {}), form?._snapshot || (() => 0));
  const child = typeof children === 'function' ? children(form!) : children;
  const element = isValidElement(child) ? child as React.ReactElement<any> : null;
  const id = element?.props.id || `${context?.name || 'field'}-${name == null ? generated : parts(name).join('-')}`;
  const field = useRef<Field>({ path: name || '', id });
  Object.assign(field.current, { path: name, id, label, rules, dependencies });
  const key = name == null ? null : keyOf(name);
  useEffect(() => {
    if (!form || key == null || name == null) return;
    form._fields.set(key, field.current);
    if (initialValue !== undefined && form.getFieldValue(name) === undefined) form.setFieldValue(name, initialValue);
    return () => { if (form._fields.get(key) === field.current) form._fields.delete(key); if ((preserve ?? context?.preserve) === false) form.setFieldValue(name, undefined); };
  }, [form, key]);
  const errors = name != null && form ? form.getFieldError(name) : [];
  const hasError = errors.length > 0 || validateStatus === 'error';
  const mandatory = required || rules?.some(entry => typeof entry !== 'function' && entry.required);
  const control = element && name != null && form ? cloneElement(element, {
    id, [valuePropName]: form.getFieldValue(name), 'aria-invalid': hasError || undefined, 'aria-required': mandatory || undefined,
    'aria-labelledby': label != null && !element.props['aria-label'] ? `${id}-label` : element.props['aria-labelledby'],
    'aria-describedby': errors.length || help ? `${id}-error` : extra ? `${id}-extra` : undefined,
    onChange: (...args: any[]) => { const value = getValueFromEvent ? getValueFromEvent(...args) : args[0]?.target ? args[0].target[valuePropName === 'checked' ? 'checked' : 'value'] : args[0]; form.setFieldValue(name, value); form._onValuesChange?.(assign({}, name, value), form.getFieldsValue()); element.props.onChange?.(...args); },
    onBlur: (event: any) => { void form.validateFields([name]).catch(() => {}); element.props.onBlur?.(event); },
  }) : child;
  if (noStyle) return <>{control}{errors.length > 0 && <span className="ui-form-item-explain-error" id={`${id}-error`} role="alert">{errors.map(text).join(' ')}</span>}</>;
  return <div className={`ui-form-item ${hasError ? 'ui-form-item-has-error' : ''} ${className}`} style={style}>
    {label != null && <div className="ui-form-item-label"><label id={`${id}-label`} htmlFor={htmlFor === undefined ? id : htmlFor} onClick={() => { if (htmlFor !== '') document.getElementById(htmlFor || id)?.focus(); }} className={mandatory ? 'ui-form-item-required' : ''}>{label}</label></div>}
    <div className="ui-form-item-control"><div className="ui-form-item-control-input">{control}</div>{(errors.length > 0 || help) && <div id={`${id}-error`} className="ui-form-item-explain-error" role="alert">{errors.length ? errors.map((error, index) => <div key={index}>{error}</div>) : help}</div>}{extra && <div className="ui-form-item-extra" id={`${id}-extra`}>{extra}</div>}</div>
  </div>;
}
export const Form = Object.assign(FormRoot, { Item: FormItem, useForm, useWatch });

const cx = (...values: Array<string | false | undefined>) => values.filter(Boolean).join(' ');
const useDisabled = (disabled?: boolean) => { const context = useContext(FormContext); return !!(disabled || context?.disabled); };
export interface InputProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'size' | 'prefix'> { size?: 'small' | 'middle' | 'large'; prefix?: React.ReactNode; suffix?: React.ReactNode; addonBefore?: React.ReactNode; addonAfter?: React.ReactNode; allowClear?: boolean | { clearIcon?: React.ReactNode }; status?: 'error' | 'warning'; variant?: 'outlined' | 'borderless' | 'filled' | 'underlined'; onPressEnter?: (event: React.KeyboardEvent<HTMLInputElement>) => void; }
const InputBase = forwardRef<HTMLInputElement, InputProps>(function InputBase(props, ref) {
  const { prefix, suffix, addonBefore, addonAfter, allowClear, size, className, status, variant, onPressEnter, onKeyDown, onChange, defaultValue, value, disabled: providedDisabled, ...rest } = props;
  const [local, setLocal] = useState(defaultValue || ''); const controlled = 'value' in props;
  const current = controlled ? value ?? '' : local, disabled = useDisabled(providedDisabled);
  const inner = useRef<HTMLInputElement | null>(null);
  const bind = (element: HTMLInputElement | null) => { inner.current = element; if (typeof ref === 'function') ref(element); else if (ref) ref.current = element; };
  const clear = () => { const input = inner.current; if (!input) return; const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set; setter?.call(input, ''); setLocal(''); input.dispatchEvent(new Event('input', { bubbles: true })); input.dispatchEvent(new Event('change', { bubbles: true })); input.focus(); };
  const node = <input {...rest} ref={bind} value={current} disabled={disabled} className={cx('ui-input', !prefix && !suffix && !allowClear && !addonBefore && !addonAfter && className, variant && `ui-input-${variant}`, size && `ui-input-${size}`, status && `ui-input-status-${status}`)} onChange={event => { setLocal(event.target.value); onChange?.(event); }} onKeyDown={event => { onKeyDown?.(event); if (event.key === 'Enter') onPressEnter?.(event); }} />;
  const affixed = prefix || suffix || allowClear ? <span className={cx('ui-input-affix-wrapper', className, variant && `ui-input-affix-wrapper-${variant}`, size && `ui-input-affix-wrapper-${size}`, disabled && 'ui-input-affix-wrapper-disabled')}><span className="ui-input-prefix">{prefix}</span>{node}<span className="ui-input-suffix">{allowClear && current !== '' && !disabled && !rest.readOnly && <button type="button" aria-label="Clear input" className="ui-input-clear-icon" onClick={clear}>{typeof allowClear === 'object' ? allowClear.clearIcon || '×' : '×'}</button>}{suffix}</span></span> : node;
  return addonBefore || addonAfter ? <span className={cx('ui-input-group-wrapper', className)}><span className="ui-input-wrapper ui-input-group">{addonBefore && <span className="ui-input-group-addon">{addonBefore}</span>}{affixed}{addonAfter && <span className="ui-input-group-addon">{addonAfter}</span>}</span></span> : affixed;
});
const Password = forwardRef<HTMLInputElement, InputProps>(function Password(props, ref) { const [visible, setVisible] = useState(false); return <InputBase {...props} ref={ref} type={visible ? 'text' : 'password'} suffix={<button type="button" className="ui-input-password-icon" aria-label={visible ? 'Hide password' : 'Show password'} disabled={useDisabled(props.disabled)} onClick={() => setVisible(!visible)}>{visible ? '◉' : '◎'}</button>} />; });
export interface TextAreaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> { autoSize?: boolean | { minRows?: number; maxRows?: number }; allowClear?: boolean; status?: string; }
const TextArea = forwardRef<HTMLTextAreaElement, TextAreaProps>(function TextArea({ autoSize, allowClear, status, className, disabled: providedDisabled, style, ...props }, ref) {
  const inner = useRef<HTMLTextAreaElement | null>(null), disabled = useDisabled(providedDisabled);
  useLayoutEffect(() => { if (!autoSize || !inner.current) return; const input = inner.current; input.style.height = 'auto'; const line = parseFloat(getComputedStyle(input).lineHeight) || 20; const min = typeof autoSize === 'object' ? autoSize.minRows || 2 : 2; const max = typeof autoSize === 'object' ? autoSize.maxRows || Infinity : Infinity; input.style.height = `${Math.max(min * line + 10, Math.min(max * line + 10, input.scrollHeight))}px`; }, [props.value, autoSize]);
  return <textarea {...props} {...('value' in props ? { value: props.value ?? '' } : {})} disabled={disabled} className={cx('ui-input', 'ui-input-textarea', className, status && `ui-input-status-${status}`)} style={style} ref={element => { inner.current = element; if (typeof ref === 'function') ref(element); else if (ref) ref.current = element; }} />;
});
const Search = forwardRef<HTMLInputElement, InputProps & { enterButton?: React.ReactNode; onSearch?: (value: string) => void }>(function Search({ enterButton, onSearch, onPressEnter, className, ...props }, ref) {
  const inner = useRef<HTMLInputElement | null>(null);
  return <span className={cx('ui-input-search', className)}><InputBase {...props} ref={element => { inner.current = element; if (typeof ref === 'function') ref(element); else if (ref) ref.current = element; }} onPressEnter={event => { onPressEnter?.(event); onSearch?.(event.currentTarget.value); }} /><button type="button" className="ui-input-search-button" disabled={useDisabled(props.disabled)} onClick={() => onSearch?.(inner.current?.value || '')}>{enterButton === true || !enterButton ? 'Search' : enterButton}</button></span>;
});
export const Input = Object.assign(InputBase, { Password, TextArea, Search });

export interface CheckboxProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'onChange' | 'size'> { onChange?: (event: React.ChangeEvent<HTMLInputElement>) => void; indeterminate?: boolean; }
const CheckboxRoot = forwardRef<HTMLInputElement, CheckboxProps>(function Checkbox({ children, className, indeterminate, disabled: providedDisabled, ...props }, ref) {
  const disabled = useDisabled(providedDisabled);
  return <label className={cx('ui-checkbox-wrapper', props.checked && 'ui-checkbox-wrapper-checked', disabled && 'ui-checkbox-wrapper-disabled', className)}><span className={cx('ui-checkbox', props.checked && 'ui-checkbox-checked', indeterminate && 'ui-checkbox-indeterminate')}><input {...props} checked={'checked' in props ? !!props.checked : undefined} type="checkbox" className="ui-checkbox-input" disabled={disabled} ref={element => { if (element) element.indeterminate = !!indeterminate; if (typeof ref === 'function') ref(element); else if (ref) ref.current = element; }} /><span className="ui-checkbox-inner" /></span>{children != null && <span>{children}</span>}</label>;
});
type GroupProps = { options?: Array<string | number | { value: any; label: React.ReactNode; disabled?: boolean }>; value?: any; defaultValue?: any; onChange?: (value: any) => void; children?: React.ReactNode; disabled?: boolean; className?: string; style?: React.CSSProperties; name?: string; };
function CheckboxGroup(props: GroupProps) { const [local, setLocal] = useState(props.defaultValue || []), value: any[] = 'value' in props ? props.value || [] : local; return <div className={cx('ui-checkbox-group', props.className)} style={props.style}>{props.options?.map((entry, index) => { const option = typeof entry === 'object' ? entry : { value: entry, label: entry }; return <CheckboxRoot key={index} checked={value.includes(option.value)} disabled={props.disabled || option.disabled} onChange={event => { const next = event.target.checked ? [...value, option.value] : value.filter(item => item !== option.value); setLocal(next); props.onChange?.(next); }}>{option.label}</CheckboxRoot>; })}{props.children}</div>; }
export const Checkbox = Object.assign(CheckboxRoot, { Group: CheckboxGroup });
const RadioContext = createContext<{ value: any; name: string; disabled?: boolean; change: (value: any, event: React.ChangeEvent<HTMLInputElement>) => void } | null>(null);
function RadioRoot({ className, children, disabled: providedDisabled, onChange, ...props }: CheckboxProps) { const context = useContext(RadioContext), disabled = useDisabled(providedDisabled || context?.disabled); return <label className={cx('ui-radio-wrapper', (context ? context.value === props.value : props.checked) && 'ui-radio-wrapper-checked', className)}><span className="ui-radio"><input {...props} className="ui-radio-input" type="radio" name={context?.name || props.name} checked={context ? context.value === props.value : props.checked} disabled={disabled} onChange={event => { onChange?.(event); context?.change(props.value, event); }} /><span className="ui-radio-inner" /></span><span>{children}</span></label>; }
function RadioGroup(props: Omit<GroupProps, 'onChange'> & { onChange?: (event: { target: { value: any } }) => void; optionType?: string; size?: string; buttonStyle?: string }) { const [local, setLocal] = useState(props.defaultValue), generated = useId(); return <RadioContext.Provider value={{ value: 'value' in props ? props.value : local, name: props.name || generated, disabled: props.disabled, change: (value) => { setLocal(value); props.onChange?.({ target: { value } }); } }}><div className={cx('ui-radio-group', props.className)} style={props.style}>{props.options?.map((entry, index) => { const option = typeof entry === 'object' ? entry : { value: entry, label: entry }; return <RadioRoot key={index} value={option.value} disabled={option.disabled}>{option.label}</RadioRoot>; })}{props.children}</div></RadioContext.Provider>; }
export const Radio = Object.assign(RadioRoot, { Group: RadioGroup, Button: (props: CheckboxProps) => <RadioRoot {...props} className={cx('ui-radio-button-wrapper', props.className)} /> });
export interface SwitchProps extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, 'onChange' | 'value'> { checked?: boolean; defaultChecked?: boolean; onChange?: (checked: boolean, event: React.MouseEvent<HTMLButtonElement>) => void; loading?: boolean; size?: 'small' | 'default'; checkedChildren?: React.ReactNode; unCheckedChildren?: React.ReactNode; }
export function Switch(allProps: SwitchProps) { const { checked, defaultChecked = false, onChange, loading, size, checkedChildren, unCheckedChildren, className, disabled: providedDisabled, ...props } = allProps; const [local, setLocal] = useState(defaultChecked), disabled = useDisabled(providedDisabled || loading), current = 'checked' in allProps ? !!checked : local; return <button {...props} type="button" role="switch" aria-checked={current} aria-busy={loading || undefined} disabled={disabled} className={cx('ui-switch', current && 'ui-switch-checked', size === 'small' && 'ui-switch-small', className)} onClick={event => { setLocal(!current); onChange?.(!current, event); props.onClick?.(event); }}><span className="ui-switch-handle" /><span className="ui-switch-inner">{current ? checkedChildren : unCheckedChildren}</span></button>; }

export interface InputNumberProps<T extends string | number = number> extends Omit<React.InputHTMLAttributes<HTMLInputElement>, 'size' | 'value' | 'defaultValue' | 'onChange' | 'min' | 'max' | 'step' | 'prefix'> { value?: T | null; defaultValue?: T; onChange?: (value: T | null) => void; min?: string | number; max?: string | number; step?: string | number; precision?: number; stringMode?: boolean; formatter?: (value: T | undefined, info?: { userTyping: boolean; input: string }) => string; parser?: (displayValue?: string) => T; controls?: boolean; size?: string; addonBefore?: React.ReactNode; addonAfter?: React.ReactNode; prefix?: React.ReactNode; }
export function InputNumber<T extends string | number = number>(props: InputNumberProps<T>) {
  const { value, defaultValue, onChange, min, max, step = 1, precision, stringMode, formatter, parser, controls = true, size, addonBefore, addonAfter, prefix, className, disabled: providedDisabled, onKeyDown, onBlur, ...rest } = props;
  const [local, setLocal] = useState<T | null>(defaultValue ?? null), [draft, setDraft] = useState(String(value ?? defaultValue ?? ''));
  const controlled = 'value' in props, current = controlled ? value : local, disabled = useDisabled(providedDisabled);
  useEffect(() => { setDraft(current == null ? '' : formatter ? formatter(current, { userTyping: false, input: String(current) }) : String(current)); }, [current]);
  const commit = (raw: string, clamp: boolean) => {
    const parsed = parser ? parser(raw) : raw.replace(/,/g, '');
    if (parsed === '' || parsed == null || !Number.isFinite(Number(parsed))) { if (!controlled) setLocal(null); onChange?.(null); return; }
    let number = Number(parsed);
    if (clamp) { if (min != null) number = Math.max(Number(min), number); if (max != null) number = Math.min(Number(max), number); if (precision != null) number = Number(number.toFixed(precision)); }
    const next = (stringMode ? clamp ? String(number) : String(parsed) : number) as T;
    if (!controlled) setLocal(next); onChange?.(next);
    if (clamp) setDraft(formatter ? formatter(next, { userTyping: false, input: String(next) }) : String(next));
  };
  return <span className={cx('ui-input-number-group-wrapper', className)} style={props.style}>{addonBefore && <span className="ui-input-group-addon">{addonBefore}</span>}<span className={cx('ui-input-number', size && `ui-input-number-${size}`, disabled && 'ui-input-number-disabled')}>
    {prefix}<input {...rest} style={undefined} type="text" inputMode={rest.inputMode || 'decimal'} className="ui-input-number-input" role="spinbutton" aria-valuemin={min == null ? undefined : Number(min)} aria-valuemax={max == null ? undefined : Number(max)} aria-valuenow={current == null ? undefined : Number(current)} value={draft} disabled={disabled} onChange={event => { setDraft(event.target.value); commit(event.target.value, false); }} onBlur={event => { commit(draft, true); onBlur?.(event); }} onKeyDown={event => { onKeyDown?.(event); if (!event.defaultPrevented && (event.key === 'ArrowUp' || event.key === 'ArrowDown')) { event.preventDefault(); commit(String(Number(current || 0) + Number(step) * (event.key === 'ArrowUp' ? 1 : -1)), true); } }} />
    {controls && <span className="ui-input-number-handler-wrap"><button type="button" tabIndex={-1} aria-label="Increase value" disabled={disabled || max != null && Number(current) >= Number(max)} onClick={() => commit(String(Number(current || 0) + Number(step)), true)}>▴</button><button type="button" tabIndex={-1} aria-label="Decrease value" disabled={disabled || min != null && Number(current) <= Number(min)} onClick={() => commit(String(Number(current || 0) - Number(step)), true)}>▾</button></span>}</span>{addonAfter && <span className="ui-input-group-addon">{addonAfter}</span>}</span>;
}

export type SelectOption = { value: string | number; label?: React.ReactNode; title?: string; disabled?: boolean; [key: string]: any };
export interface SelectProps<T = any> extends Omit<React.HTMLAttributes<HTMLDivElement>, 'onChange' | 'defaultValue' | 'onSelect' | 'prefix'> { value?: T; defaultValue?: T; options?: SelectOption[]; children?: React.ReactNode; onChange?: (value: T, option?: SelectOption | SelectOption[]) => void; onSelect?: (value: any, option: SelectOption) => void; onDeselect?: (value: any, option: SelectOption) => void; mode?: 'multiple' | 'tags'; showSearch?: boolean; filterOption?: boolean | ((input: string, option?: SelectOption) => boolean); optionFilterProp?: string; onSearch?: (value: string) => void; placeholder?: React.ReactNode; allowClear?: boolean; disabled?: boolean; loading?: boolean; size?: string; open?: boolean; onOpenChange?: (open: boolean) => void; onDropdownVisibleChange?: (open: boolean) => void; popupClassName?: string; dropdownClassName?: string; popupMatchSelectWidth?: boolean | number; listHeight?: number; onPopupScroll?: (event: React.UIEvent<HTMLDivElement>) => void; suffixIcon?: React.ReactNode; maxTagCount?: number | 'responsive'; notFoundContent?: React.ReactNode; labelInValue?: boolean; }
function SelectRoot<T = any>(props: SelectProps<T>) {
  const { value, defaultValue, options: supplied, children, onChange, onSelect, onDeselect, mode, showSearch, filterOption, optionFilterProp = 'label', onSearch, placeholder, allowClear, loading, size, open, onOpenChange, onDropdownVisibleChange, popupClassName, dropdownClassName, popupMatchSelectWidth, listHeight = 256, onPopupScroll, suffixIcon, maxTagCount, notFoundContent, labelInValue, className, style, disabled: providedDisabled, onKeyDown, onClick, ...rest } = props;
  const disabled = useDisabled(providedDisabled), multiple = !!mode, searchable = !!(showSearch || multiple);
  const [local, setLocal] = useState<any>(defaultValue), [expanded, setExpanded] = useState(false), [query, setQuery] = useState(''), [active, setActive] = useState(0), [position, setPosition] = useState<React.CSSProperties>({});
  const wrapper = useRef<HTMLDivElement>(null), popup = useRef<HTMLDivElement>(null), input = useRef<HTMLInputElement>(null), listId = useId();
  const current: any = 'value' in props ? value : local, isOpen = !disabled && (open ?? expanded);
  const options: SelectOption[] = supplied || Children.toArray(children).filter(isValidElement).map((child: any) => ({ ...child.props, label: child.props.children }));
  const raw = (item: any) => labelInValue ? item?.value : item;
  const selected: any[] = multiple ? Array.isArray(current) ? current.map(raw) : [] : current === undefined || current === null ? [] : [raw(current)];
  const allOptions: SelectOption[] = [...options, ...selected.filter(item => !options.some(option => option.value === item)).map(item => ({ value: item, label: String(item) }))];
  const filtered = allOptions.filter(option => !query || filterOption === false || (typeof filterOption === 'function' ? filterOption(query, option) : text(option[optionFilterProp] ?? option.label ?? option.value).toLowerCase().includes(query.toLowerCase())));
  const changeOpen = (next: boolean) => { if (disabled) return; setExpanded(next); onOpenChange?.(next); onDropdownVisibleChange?.(next); if (!next) { setQuery(''); onSearch?.(''); } };
  const publish = (items: any[]) => {
    const details = items.map(item => allOptions.find(option => option.value === item) || { value: item, label: String(item) });
    const outgoing = labelInValue ? details.map(option => ({ value: option.value, label: option.label })) : items;
    const next = multiple ? outgoing : outgoing[0]; setLocal(next); onChange?.(next as T, multiple ? details : details[0]);
  };
  const choose = (option: SelectOption) => { if (option.disabled) return; if (multiple) { const removing = selected.includes(option.value); publish(removing ? selected.filter(item => item !== option.value) : [...selected, option.value]); if (removing) onDeselect?.(option.value, option); else onSelect?.(option.value, option); setQuery(''); onSearch?.(''); input.current?.focus(); } else { publish([option.value]); onSelect?.(option.value, option); changeOpen(false); if (!searchable) wrapper.current?.focus(); } };
  useLayoutEffect(() => {
    if (!isOpen || !wrapper.current) return;
    const place = () => {
      const rect = wrapper.current!.getBoundingClientRect();
      const below = innerHeight - rect.bottom - 8, above = rect.top - 8;
      const upward = below < Math.min(listHeight, 160) && above > below;
      const height = Math.max(40, Math.min(listHeight, upward ? above : below));
      const width = Math.min(innerWidth - 16, typeof popupMatchSelectWidth === 'number' ? popupMatchSelectWidth : popupMatchSelectWidth === false ? Math.max(rect.width, 180) : rect.width);
      let layer = 1100;
      for (let element: HTMLElement | null = wrapper.current; element; element = element.parentElement) layer = Math.max(layer, (Number(getComputedStyle(element).zIndex) || 0) + 1);
      setPosition({ position: 'fixed', zIndex: layer, left: Math.max(8, Math.min(rect.left, innerWidth - width - 8)), top: upward ? Math.max(8, rect.top - height - 4) : rect.bottom + 4, width, maxHeight: height });
    };
    place(); window.addEventListener('resize', place); window.addEventListener('scroll', place, true);
    return () => { window.removeEventListener('resize', place); window.removeEventListener('scroll', place, true); };
  }, [isOpen, listHeight, popupMatchSelectWidth]);
  useEffect(() => { if (!isOpen) return; const outside = (event: PointerEvent) => { if (!wrapper.current?.contains(event.target as Node) && !popup.current?.contains(event.target as Node)) changeOpen(false); }; document.addEventListener('pointerdown', outside); return () => document.removeEventListener('pointerdown', outside); }, [isOpen]);
  useEffect(() => { setActive(0); }, [query]);
  const keyboard = (event: React.KeyboardEvent<HTMLDivElement>) => {
    onKeyDown?.(event); if (event.defaultPrevented || disabled) return;
    if (event.key === 'Escape' && isOpen) { event.preventDefault(); event.stopPropagation(); changeOpen(false); if (!searchable) wrapper.current?.focus(); }
    if (event.key === 'Tab') changeOpen(false);
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') { event.preventDefault(); if (!isOpen) changeOpen(true); let next = active; for (let count = 0; count < filtered.length; count++) { next = (next + (event.key === 'ArrowDown' ? 1 : -1) + filtered.length) % filtered.length; if (!filtered[next].disabled) break; } setActive(next); requestAnimationFrame(() => document.getElementById(`${listId}-${next}`)?.scrollIntoView({ block: 'nearest' })); }
    if (event.key === 'Enter') { event.preventDefault(); if (!isOpen) { changeOpen(true); return; } if (mode === 'tags' && query.trim() && !filtered.some(option => option.value === query.trim())) { choose({ value: query.trim(), label: query.trim() }); } else if (filtered[active]) choose(filtered[active]); }
    if (event.key === 'Backspace' && multiple && !query && selected.length) publish(selected.slice(0, -1));
  };
  return <div {...rest} id={searchable ? undefined : rest.id} aria-label={searchable ? undefined : props['aria-label']} ref={wrapper} tabIndex={searchable ? -1 : disabled ? -1 : 0} role={searchable ? undefined : 'combobox'} aria-haspopup="listbox" aria-expanded={isOpen} aria-controls={isOpen ? listId : undefined} aria-activedescendant={isOpen ? `${listId}-${active}` : undefined} aria-disabled={disabled} className={cx('ui-select', multiple ? 'ui-select-multiple' : 'ui-select-single', isOpen && 'ui-select-open', disabled && 'ui-select-disabled', size && `ui-select-${size}`, className)} style={style} onKeyDown={keyboard} onClick={event => { event.stopPropagation(); onClick?.(event); if (!isOpen) changeOpen(true); else if (!searchable) changeOpen(false); input.current?.focus(); }}>
    <div className="ui-select-selector"><span className="ui-select-selection-wrap">
      {multiple ? selected.slice(0, typeof maxTagCount === 'number' ? maxTagCount : selected.length).map(item => <span className="ui-select-selection-item" key={String(item)}><span className="ui-select-selection-item-content">{allOptions.find(option => option.value === item)?.label ?? String(item)}</span><button type="button" className="ui-select-selection-item-remove" aria-label={`Remove ${text(allOptions.find(option => option.value === item)?.label ?? String(item))}`} disabled={disabled} onClick={event => { event.stopPropagation(); publish(selected.filter(value => value !== item)); }}>×</button></span>) : !query && selected.length ? <span className="ui-select-selection-item">{allOptions.find(option => option.value === selected[0])?.label ?? String(selected[0])}</span> : null}
      {!selected.length && !query && <span className="ui-select-selection-placeholder">{placeholder}</span>}
      {searchable && <span className="ui-select-selection-search"><input {...(rest.id ? { id: rest.id } : {})} ref={input} role="combobox" aria-label={props['aria-label'] || (text(placeholder) || undefined)} aria-labelledby={props['aria-labelledby']} aria-invalid={props['aria-invalid']} aria-describedby={props['aria-describedby']} aria-required={props['aria-required']} aria-expanded={isOpen} aria-controls={isOpen ? listId : undefined} aria-activedescendant={isOpen ? `${listId}-${active}` : undefined} aria-autocomplete="list" autoComplete="off" value={query} disabled={disabled} className="ui-select-selection-search-input" onFocus={() => changeOpen(true)} onChange={event => { setQuery(event.target.value); onSearch?.(event.target.value); changeOpen(true); }} /></span>}
    </span></div>{allowClear && selected.length > 0 && !disabled ? <button type="button" className="ui-select-clear" aria-label="Clear selection" onClick={event => { event.stopPropagation(); publish([]); }}>×</button> : <span className="ui-select-arrow" aria-hidden="true">{loading ? '◌' : suffixIcon || '⌄'}</span>}
    {isOpen && createPortal(<div ref={popup} className={cx('ui-select-dropdown', popupClassName, dropdownClassName)} style={position} onScroll={onPopupScroll} onPointerDown={event => { if ((event.target as Element).closest('[role="option"]')) event.preventDefault(); }}><div role="listbox" id={listId} aria-multiselectable={multiple || undefined}>{filtered.length ? filtered.map((option, index) => <div key={String(option.value)} id={`${listId}-${index}`} role="option" aria-selected={selected.includes(option.value)} aria-disabled={option.disabled || undefined} className={cx('ui-select-item', 'ui-select-item-option', index === active && 'ui-select-item-option-active', selected.includes(option.value) && 'ui-select-item-option-selected', option.disabled && 'ui-select-item-option-disabled')} onMouseMove={() => setActive(index)} onClick={() => choose(option)}><span className="ui-select-item-option-content">{option.label ?? String(option.value)}</span>{selected.includes(option.value) && <span aria-hidden="true">✓</span>}</div>) : <div className="ui-select-empty">{notFoundContent ?? 'No options found'}</div>}</div></div>, document.body)}
  </div>;
}
export const Select = SelectRoot;

export interface TimePickerProps { value?: Dayjs | null; defaultValue?: Dayjs; onChange?: (value: Dayjs | null, formatted: string | string[]) => void; format?: string; minuteStep?: number; needConfirm?: boolean; allowClear?: boolean; disabled?: boolean; size?: string; className?: string; style?: React.CSSProperties; popupClassName?: string; id?: string; 'aria-label'?: string; }
export function TimePicker({ value, defaultValue, onChange, minuteStep = 1, needConfirm, allowClear = true, disabled: providedDisabled, className, style, id, 'aria-label': label = 'Time' }: TimePickerProps) {
  const [local, setLocal] = useState(defaultValue?.format('HH:mm') || ''), disabled = useDisabled(providedDisabled);
  const controlled = value !== undefined, current = controlled ? value?.format('HH:mm') || '' : local;
  useEffect(() => { if (controlled) setLocal(value?.format('HH:mm') || ''); }, [value?.valueOf()]);
  const publish = (next: string) => { setLocal(next); const [hour, minute] = next.split(':').map(Number); onChange?.(next ? dayjs().hour(hour).minute(minute).second(0) : null, next); };
  return <span className={cx('ui-picker', className)} style={style}><span className="ui-picker-input"><input id={id} aria-label={label} type="time" value={needConfirm ? local : current} step={minuteStep * 60} disabled={disabled} onChange={event => { if (needConfirm) setLocal(event.target.value); else publish(event.target.value); }} /></span>{allowClear && current && <button type="button" aria-label="Clear time" disabled={disabled} onClick={() => publish('')}>×</button>}{needConfirm && local !== current && <button type="button" disabled={disabled} onMouseDown={event => event.preventDefault()} onClick={() => publish(local)}>Apply</button>}</span>;
}

type UploadFile = File & { uid?: string };
export interface UploadProps { children?: React.ReactNode; accept?: string; disabled?: boolean; multiple?: boolean; showUploadList?: boolean; beforeUpload?: (file: UploadFile, files: UploadFile[]) => boolean | void | File | Promise<boolean | void | File>; customRequest?: (options: { file: File; onSuccess: (body?: any) => void; onError: (error: Error) => void }) => void; onChange?: (info: { file: any; fileList: any[] }) => void; onRemove?: (file: any) => boolean | void | Promise<boolean | void>; action?: string; method?: string; headers?: Record<string, string>; data?: Record<string, any>; maxCount?: number; className?: string; style?: React.CSSProperties; name?: string; }
function UploadRoot({ children, accept, disabled: providedDisabled, multiple, showUploadList = true, beforeUpload, customRequest, onChange, onRemove, action, method = 'POST', headers, data, maxCount, className, style, name = 'file' }: UploadProps) {
  const disabled = useDisabled(providedDisabled), picker = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<any[]>([]);
  const selectFiles = async (list: File[]) => {
    for (const original of list) {
      const result = await beforeUpload?.(original, list);
      if (result === false) continue;
      const file = result instanceof File ? result : original;
      const record = { uid: crypto.randomUUID(), name: file.name, originFileObj: file, status: action || customRequest ? 'uploading' : undefined };
      setFiles(current => { const next = [...current, record].slice(maxCount ? -maxCount : 0); onChange?.({ file: record, fileList: next }); return next; });
      const done = (status: 'done' | 'error', response?: any) => setFiles(current => { const next = current.map(item => item.uid === record.uid ? { ...item, status, response } : item); onChange?.({ file: { ...record, status, response }, fileList: next }); return next; });
      if (customRequest) customRequest({ file, onSuccess: body => done('done', body), onError: error => done('error', error.message) });
      else if (action) { try { const body = new FormData(); body.append(name, file); Object.entries(data || {}).forEach(([key, value]) => body.append(key, String(value))); const response = await fetch(action, { method, headers, body }); if (!response.ok) throw new Error(`Upload failed (HTTP ${response.status})`); done('done', await response.text()); } catch (error) { done('error', error instanceof Error ? error.message : 'Upload failed'); } }
    }
  };
  return <span className={cx('ui-upload-wrapper', className)} style={style}><span className="ui-upload" onClick={event => { if (!disabled && event.target !== picker.current) picker.current?.click(); }}><input ref={picker} type="file" accept={accept} multiple={multiple} disabled={disabled} hidden onChange={event => { void selectFiles(Array.from(event.target.files || [])).finally(() => { if (picker.current) picker.current.value = ''; }); }} />{children}</span>{showUploadList && <span className="ui-upload-list">{files.map(file => <span key={file.uid} className={`ui-upload-list-item is-${file.status || 'selected'}`}><span>{file.name}</span>{file.status === 'error' && <span role="alert">{file.response}</span>}<button type="button" disabled={disabled} aria-label={`Remove ${file.name}`} onClick={async () => { if (await onRemove?.(file) === false) return; setFiles(current => current.filter(item => item.uid !== file.uid)); }}>×</button></span>)}</span>}</span>;
}
export const Upload = UploadRoot;
