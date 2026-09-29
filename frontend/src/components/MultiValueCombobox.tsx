import { useId, useState } from 'react'

export interface MultiValueOption {
  value: string
  label?: string
}

interface MultiValueComboboxProps {
  label: string
  values: string[]
  options: MultiValueOption[]
  placeholder: string
  hint: string
  selectedLabel: string
  addLabel: string
  removeLabel: string
  onChange: (values: string[]) => void
}

function uniqueValues(values: string[]) {
  return [...new Set(values.map((value) => value.trim()).filter(Boolean))]
}

export function MultiValueCombobox({
  label,
  values,
  options,
  placeholder,
  hint,
  selectedLabel,
  addLabel,
  removeLabel,
  onChange,
}: MultiValueComboboxProps) {
  const [draft, setDraft] = useState('')
  const inputId = useId()
  const listId = `${inputId.replaceAll(':', '')}-options`

  const addValues = (incoming: string[]) => {
    const next = uniqueValues([...values, ...incoming])
    if (
      next.length !== values.length ||
      next.some((value, index) => value !== values[index])
    ) {
      onChange(next)
    }
    setDraft('')
  }
  const addDraft = () => addValues(draft.split(','))

  return (
    <div>
      <label className="block" htmlFor={inputId}>
        {label}
      </label>
      <div className="mt-1 rounded border border-slate-200 bg-slate-50 p-1.5 focus-within:border-sky-500/60">
        <div
          aria-label={selectedLabel}
          className="mb-1 flex min-h-7 flex-wrap gap-1"
        >
          {values.map((value) => (
            <span
              key={value}
              className="inline-flex max-w-full items-center gap-1 rounded bg-sky-400/10 px-2 py-1 text-[11px] text-indigo-600"
            >
              <span className="truncate font-mono">{value}</span>
              <button
                type="button"
                aria-label={`${removeLabel}: ${value}`}
                className="rounded px-0.5 text-indigo-600 hover:bg-sky-400/15 hover:text-indigo-600"
                onClick={() =>
                  onChange(values.filter((item) => item !== value))
                }
              >
                ×
              </button>
            </span>
          ))}
        </div>
        <div className="flex gap-1">
          <input
            id={inputId}
            aria-label={label}
            autoComplete="off"
            className="min-w-0 flex-1 bg-transparent px-1 py-1 text-xs text-slate-800 outline-none placeholder:text-slate-600"
            list={listId}
            placeholder={placeholder}
            value={draft}
            onChange={(event) => {
              const next = event.target.value
              const selectedOption = options.find(
                (option) => option.value === next,
              )
              if (selectedOption) addValues([selectedOption.value])
              else setDraft(next)
            }}
            onKeyDown={(event) => {
              if (event.nativeEvent.isComposing || event.keyCode === 229) return
              if (event.key === 'Enter' || event.key === ',') {
                event.preventDefault()
                addDraft()
              }
              if (event.key === 'Backspace' && !draft && values.length) {
                onChange(values.slice(0, -1))
              }
            }}
          />
          <button
            type="button"
            aria-label={addLabel}
            className="rounded border border-slate-200 px-2 py-1 text-[11px] text-slate-600 hover:border-sky-500/50 hover:text-indigo-600 disabled:cursor-not-allowed disabled:opacity-35"
            disabled={!draft.trim()}
            onClick={addDraft}
          >
            +
          </button>
        </div>
        <datalist id={listId}>
          {options
            .filter((option) => !values.includes(option.value))
            .map((option) => (
              <option
                key={option.value}
                value={option.value}
                label={option.label}
              />
            ))}
        </datalist>
      </div>
      <p className="mt-1 text-[10px] leading-4 text-slate-600">{hint}</p>
    </div>
  )
}
