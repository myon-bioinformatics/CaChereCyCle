document.getElementById("capacity-file").addEventListener("change", event => {
  const file = event.target.files[0];
  const output = document.getElementById("capacity-result");
  if (!file) {
    output.textContent = "";
    return;
  }
  const length = 4 * Math.ceil(file.size / 3);
  output.textContent = `${file.name}: ${file.size} bytes → Base64 ${length}文字（PNG data URIなら ${length + 22}文字）`;
});
