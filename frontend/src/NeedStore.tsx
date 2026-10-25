import type { ReactNode } from 'react'
import { useApp, type StoreRow } from './state'
import { Card, Empty, PageHead } from './ui'

/** Store-level pages render only with one store selected — data from different stores is never mixed. */
export function NeedStore({ title, children }: { title: string; children: (s: StoreRow) => ReactNode }) {
  const { store, stores, set } = useApp()
  if (store) return <>{children(store)}</>
  return (
    <>
      <PageHead title={title} />
      <Card>
        <Empty title="Choose a store">
          <p>This analysis runs on one store at a time so data from different stores is never mixed.</p>
          <div className="row" style={{ justifyContent: 'center' }}>
            {stores.map((s) => <button key={s.id} onClick={() => set({ storeId: s.id })}>{s.code}{s.is_demo ? ' (demo)' : ''}</button>)}
          </div>
        </Empty>
      </Card>
    </>
  )
}
