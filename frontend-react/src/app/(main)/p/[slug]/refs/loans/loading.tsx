export default function LoansLoading() {
    return (
        <div className="animate-in">
            <div className="page-header">
                <div className="skeleton" style={{ width: 180, height: 32, borderRadius: 8 }} />
            </div>
            <div className="glass-card" style={{ padding: 32 }}>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                    {[1, 2, 3].map(i => (
                        <div key={i} className="skeleton" style={{ height: 40, borderRadius: 8 }} />
                    ))}
                </div>
            </div>
        </div>
    );
}
