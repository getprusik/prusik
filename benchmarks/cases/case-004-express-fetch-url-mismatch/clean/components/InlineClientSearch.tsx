import { useState } from "react";

export function InlineClientSearch() {
  const [q, setQ] = useState("");
  const onClick = () => {
    fetch("/invoices/clients/search?q=" + encodeURIComponent(q))
      .then(r => r.json())
      .then(data => console.log(data));
  };
  return (
    <div>
      <button type="button" onClick={onClick}>Search</button>
    </div>
  );
}
