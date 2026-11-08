import {useState} from 'react';
import {Paperclip} from 'lucide-react';
import {PreviewRows} from './file-inspection';

export const receiptFileTypes = '.pdf,.jpg,.jpeg,.png,.bmp,.webp,.tif,.tiff,.heic';
export type ReceiptAction = {type:'attach_receipt'} | {
  type:'select_fields';
  options:string[];
  detected_fields?:{label:string; value?:string|null}[];
  has_readable_text?:boolean;
  item_details?:{label:string; options:string[]; selected_by_default:boolean; preview:Record<string,string|null>[]};
};

type Props = {
  text: string;
  action: ReceiptAction;
  disabled: boolean;
  onAttach: () => void;
  onLibrary: () => void;
  onAnswer: (answer: string) => void;
};

export function ReceiptQuestion({text, action, disabled, onAttach, onLibrary, onAnswer}: Props) {
  const [summaryChoice, setSummaryChoice] = useState<string[]|null>(null);
  const [other, setOther] = useState('');
  const [itemsChoice, setItemsChoice] = useState<string[]|null>(null);

  if (action.type === 'attach_receipt') {
    return <div className="as-choice-group">
      <p>{text}</p>
      <button type="button" className="as-button" disabled={disabled} onClick={onAttach}>
        <Paperclip size={15} /> 영수증 파일 첨부
      </button>
      <button type="button" className="as-button" disabled={disabled} onClick={onLibrary}>자료함에서 선택</button>
    </div>;
  }

  const detected = new Map(action.detected_fields?.map(field=>[field.label,field.value]));
  const selected = summaryChoice ?? (action.item_details ? action.options.filter(field=>
    ['날짜','상호명','결제 금액'].includes(field) && detected.has(field)) : []);
  const fields = [...new Set([...selected, ...other.split(',').map(value => value.trim()).filter(Boolean)])];
  const items = action.item_details, options = items?.options || [];
  const selectedItems = options.filter(column=>itemsChoice ? itemsChoice.includes(column) : items?.selected_by_default);
  const answer = [
    fields.length ? '영수증에서 추출할 항목: ' + fields.join(', ') : '',
    selectedItems.length ? '영수증 품목 시트: ' + selectedItems.join(', ') : '',
  ].filter(Boolean).join('\n');
  return <form className="as-choice-group as-receipt-fields" onSubmit={event => {
    event.preventDefault();
    if (answer && !disabled) onAnswer(answer);
  }}>
    <fieldset disabled={disabled}>
      <legend>{text}</legend>
      {action.has_readable_text===false&&<p>영수증에서 글자를 읽지 못했어요. <button type="button" onClick={onAttach}>다른 영수증 첨부</button></p>}
      {items && <p><strong>영수증 요약</strong> · 영수증 한 장당 한 행</p>}
      <div className="as-receipt-options">
        {action.options.map(option => <label key={option}>
          <input type="checkbox" checked={selected.includes(option)} onChange={event => {
            const checked = event.target.checked;
            setSummaryChoice(checked ? [...selected, option] : selected.filter(value=>value!==option));
          }}/>{option}{action.detected_fields && <small>{detected.get(option) ||
            (detected.has(option)?'파일별 값이 다름':'원본에서 확인 안 됨')}</small>}
        </label>)}
      </div>
      <label className="as-field">다른 항목 추가
        <input value={other} maxLength={1000} placeholder="예: 부가세, 승인번호" onChange={event => setOther(event.target.value)}/>
      </label>
      {items && <div className="as-receipt-items">
        <p><strong>{items.label}</strong> · 상품 하나당 한 행</p>
        <PreviewRows label="원본에서 읽은 구매 내역" columns={options} rows={items.preview}/>
        {!items.preview.length&&<p>원본에서 확인된 상품 행이 없어요.</p>}
        {options.length>0&&<><div className="as-receipt-options">{options.map(column=>
          <label key={column}><input type="checkbox" checked={selectedItems.includes(column)} onChange={event=>
            setItemsChoice(event.target.checked?[...selectedItems,column]:selectedItems.filter(name=>name!==column))}/>{column}</label>
        )}</div><button type="button" onClick={()=>setItemsChoice(selectedItems.length?[]:options)}>
          {selectedItems.length?'품목 열 선택 해제':'품목 열 전체 선택'}
        </button></>}
      </div>}
      <button type="submit" disabled={!answer}>선택한 항목으로 계속</button>
    </fieldset>
  </form>;
}
