export default function CounterpartyLoading() {
    return (
        <div className="animate-in">
            <div className="page-header">
                <div className="skeleton" style={{ width: 240, height: 32, borderRadius: 8 }} />
            </div>
            <div className="glass-card" style={{ padding: 32 }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                    {[1, 2, 3, 4, 5].map(i => (
                        <div key={i} className="skeleton" style={{ height: 40, borderRadius: 8 }} />
                    ))}
                </div>
            </div>
        </div>
    );
}
