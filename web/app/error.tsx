"use client";
export default function ErrorPage({reset}:{error:Error; reset:()=>void}) {
  return <main className="fatal-error"><h1>Nie udało się wyświetlić monitora</h1><p>Nie pokazujemy danych zastępczych. Spróbuj ponownie załadować interfejs.</p><button onClick={reset}>Spróbuj ponownie</button></main>;
}
