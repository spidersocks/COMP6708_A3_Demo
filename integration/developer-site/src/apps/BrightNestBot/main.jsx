import React from "react";
import { createRoot } from "react-dom/client";
import BrightNestBotDemo from "./BrightNestBotDemo.jsx";
import "./brightnest.css";

createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrightNestBotDemo />
  </React.StrictMode>
);
