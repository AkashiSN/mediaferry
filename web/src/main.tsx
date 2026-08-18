import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

// 画面は Task 13 以降で作る。ここは足場が通ることだけを示す最小の入口。
function App() {
  return <main>mediaferry</main>;
}

const root = document.getElementById("root");
if (root === null) {
  throw new Error("#root が無い");
}
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
