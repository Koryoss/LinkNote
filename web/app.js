const apiUrl = "http://127.0.0.1:8000";
const uploadBtn = document.getElementById("uploadBtn");
const queryBtn = document.getElementById("queryBtn");
const uploadStatus = document.getElementById("uploadStatus");
const queryStatus = document.getElementById("queryStatus");
const answerText = document.getElementById("answerText");
const sourceList = document.getElementById("sourceList");

const getValue = (id) => document.getElementById(id).value.trim();

uploadBtn.addEventListener("click", async () => {
  const user_id = getValue("user_id");
  const semester = getValue("semester");
  const course = getValue("course");
  const title = getValue("title");
  const unit = getValue("unit");
  const fileInput = document.getElementById("file");
  const file = fileInput.files[0];

  uploadStatus.textContent = "업로드 준비 중...";

  const missing = [];
  if (!user_id) missing.push("사용자명(맨 위 1번)");
  if (!semester) missing.push("학기");
  if (!course) missing.push("과목");
  if (!title) missing.push("자료명");
  if (!file) missing.push("PDF 파일");
  if (missing.length) {
    uploadStatus.textContent = "비어 있는 항목: " + missing.join(", ");
    return;
  }

  const formData = new FormData();
  formData.append("user_id", user_id);
  formData.append("semester", semester);
  formData.append("course", course);
  formData.append("title", title);
  formData.append("unit", unit);
  formData.append("file", file);

  try {
    const response = await fetch(`${apiUrl}/ingest`, {
      method: "POST",
      body: formData,
    });

    const data = await response.json();

    if (!response.ok) {
      uploadStatus.textContent = data.detail || "업로드에 실패했습니다.";
      return;
    }

    uploadStatus.textContent = `${data.message} (페이지 ${data.pages || 0})`;
  } catch (error) {
    uploadStatus.textContent = `API 호출 오류: ${error.message}`;
  }
});

queryBtn.addEventListener("click", async () => {
  const user_id = getValue("user_id");
  const question = getValue("question");
  const mode = getValue("mode");
  const search_filter = {
    semester: getValue("filter_semester") || undefined,
    course: getValue("filter_course") || undefined,
    filename: getValue("filter_filename") || undefined,
  };

  queryStatus.textContent = "질문을 전송 중입니다...";
  answerText.textContent = "";
  sourceList.innerHTML = "";

  if (!user_id || !question) {
    queryStatus.textContent = "user_id와 질문을 입력해주세요.";
    return;
  }

  const payload = {
    user_id,
    question,
    mode,
    search_filter,
    search_scope_label: "",
  };

  try {
    const response = await fetch(`${apiUrl}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    const data = await response.json();

    if (!response.ok) {
      queryStatus.textContent = data.detail || "질문 처리에 실패했습니다.";
      return;
    }

    queryStatus.textContent = "질문이 처리되었습니다.";
    answerText.textContent = data.answer || "답변이 없습니다.";

    if (Array.isArray(data.sources) && data.sources.length > 0) {
      sourceList.innerHTML = data.sources
        .map((item) => {
          return `<div class="source"><strong>${item.course}</strong><br />${item.title || item.filename}<br />${item.semester} · p.${item.page}</div>`;
        })
        .join("");
    } else {
      sourceList.innerHTML = "참고 자료가 없습니다.";
    }
  } catch (error) {
    queryStatus.textContent = `API 호출 오류: ${error.message}`;
  }
});
