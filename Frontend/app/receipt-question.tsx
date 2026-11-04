import {useState} from 'react';
import {Paperclip} from 'lucide-react';

export const receiptFileTypes = '.pdf,.jpg,.jpeg,.png,.bmp,.webp,.tif,.tiff,.heic';
export type ReceiptAction = {type:'attach_receipt'} | {type:'select_fields'; options:string[]};

type Props = {
  text: string;
  action: ReceiptAction;
  disabled: boolean;
  onAttach: () => void;
  onAnswer: (answer: string) => void;
};

export function ReceiptQuestion({text, action, disabled, onAttach, onAnswer}: Props) {
  const [selected, setSelected] = useState<string[]>([]);
  const [other, setOther] = useState('');

  if (action.type === 'attach_receipt') {
    return <div className="as-choice-group">
      <p>{text}</p>
      <button type="button" className="as-button" disabled={disabled} onClick={onAttach}>
        <Paperclip size={15} /> 영수증 파일 첨부
      </button>
    </div>;
  }

  const fields = [...new Set([...selected, ...other.split(',').map(value => value.trim()).filter(Boolean)])];
  return <form className="as-choice-group as-receipt-fields" onSubmit={event => {
    event.preventDefault();
    if (fields.length && !disabled) onAnswer('영수증에서 추출할 항목: ' + fields.join(', '));
  }}>
    <fieldset disabled={disabled}>
      <legend>{text}</legend>
      <div className="as-receipt-options">
        {action.options.map(option => <label key={option}>
          <input type="checkbox" checked={selected.includes(option)} onChange={event => {
            const checked = event.target.checked;
            setSelected(prior => checked ? [...prior, option] : prior.filter(value => value !== option));
          }}/>{option}
        </label>)}
      </div>
      <label className="as-field">다른 항목 추가
        <input value={other} maxLength={1000} placeholder="예: 부가세, 승인번호" onChange={event => setOther(event.target.value)}/>
      </label>
      <button type="submit" disabled={!fields.length}>선택한 항목으로 계속</button>
    </fieldset>
  </form>;
}
