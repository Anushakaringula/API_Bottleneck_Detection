import "./globals.css";

export const metadata = {
  title: "API Bottleneck Dashboard",
  description: "Detection, diagnosis and verified remediation of API performance bottlenecks",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>
        <header className="topbar">
          <a href="/" className="brand">API Bottleneck Dashboard</a>
          <span className="muted">Observe → Detect → Diagnose → Remediate → Verify</span>
        </header>
        <main className="page">{children}</main>
      </body>
    </html>
  );
}
