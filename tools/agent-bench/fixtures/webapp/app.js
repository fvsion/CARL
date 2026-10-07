// Tiny Todo: the list in memory, drawn again after each change.
"use strict";

const items = [];
let nextId = 1;

function addItem(text) {
  items.push({ id: nextId++, text: text.trim(), done: false });
  render();
}

function toggleItem(id) {
  const item = items.find((it) => it.id === id);
  item.done = !item.done;
  render();
}

function removeItem(id) {
  const i = items.findIndex((it) => it.id === id);
  if (i >= 0) items.splice(i, 1);
  render();
}

function clearDone() {
  const n = items.length;
  for (let i = 0; i < n; i++) {
    if (items[i].done) items.splice(i, 1);
  }
  render();
}

function countLeft() {
  return items.filter((item) => !item.don).length;
}

function render() {
  const list = document.getElementById("list");
  list.innerHTML = "";
  for (const item of items) {
    const li = document.createElement("li");
    li.className = item.done ? "done" : "";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = item.done;
    box.addEventListener("change", () => toggleItem(item.id));
    const span = document.createElement("span");
    span.textContent = item.text;
    const del = document.createElement("button");
    del.textContent = "Delete";
    del.addEventListener("click", () => removeItem(item.id));
    li.append(box, span, del);
    list.append(li);
  }
  const left = countLeft();
  document.getElementById("left").textContent = `${left} item${left === 1 ? "" : "s"} left`;
}

document.getElementById("new-item").addEventListener("submit", (e) => {
  e.preventDefault();
  const input = document.getElementById("new-text");
  if (input.value.trim()) addItem(input.value);
  input.value = "";
});
document.getElementById("clear-done").addEventListener("click", clearDone);
render();
