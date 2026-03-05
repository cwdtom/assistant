#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: bash .codex/skills/be-skill/scripts/quick-check.sh <skill-name>"
  exit 1
fi

skill_name="$1"
skill_dir="skills/${skill_name}"
skill_md="${skill_dir}/SKILL.md"
refs_dir="${skill_dir}/references"
official_docs_dir="${refs_dir}/official-docs"
index_file="${refs_dir}/official-docs-fallback.md"

if [[ ! -d "${skill_dir}" ]]; then
  echo "[ERROR] Skill directory not found: ${skill_dir}"
  exit 1
fi

if [[ ! -f "${skill_md}" ]]; then
  echo "[ERROR] Missing SKILL.md: ${skill_md}"
  exit 1
fi

if [[ ! -d "${refs_dir}" ]]; then
  echo "[ERROR] Missing references directory: ${refs_dir}"
  exit 1
fi

echo "[1/6] File tree under ${skill_dir}"
find "${skill_dir}" -maxdepth 4 -type f | sort
echo

echo "[2/6] Required file structure check"
structure_issue=0
required_ref_files=(
  "skill-template.md"
  "reference-template.md"
  "quality-checklist.md"
  "official-docs-fallback.md"
)

for ref_file in "${required_ref_files[@]}"; do
  if [[ ! -f "${refs_dir}/${ref_file}" ]]; then
    echo "[FAIL] Missing required reference file: ${refs_dir}/${ref_file}"
    structure_issue=1
  fi
done

if [[ ! -d "${official_docs_dir}" ]]; then
  echo "[FAIL] Missing official docs directory: ${official_docs_dir}"
  structure_issue=1
else
  docs_count="$(find "${official_docs_dir}" -maxdepth 1 -type f -name "*.md" | wc -l | tr -d '[:space:]')"
  if [[ "${docs_count}" == "0" ]]; then
    echo "[FAIL] No official docs files found under ${official_docs_dir}"
    structure_issue=1
  fi
fi

if [[ ${structure_issue} -eq 0 ]]; then
  echo "[PASS] Required files and official docs directory are present."
fi
echo

echo "[3/6] Path compliance scan"
path_issue=0
path_pattern="(/Users/|/home/|[A-Za-z]:\\\\|\\.\\./)"
if command -v rg >/dev/null 2>&1; then
  if rg -n "${path_pattern}" "${skill_md}" "${refs_dir}"; then
    path_issue=1
  fi
else
  if grep -RInE "${path_pattern}" "${skill_md}" "${refs_dir}"; then
    path_issue=1
  fi
fi

if [[ ${path_issue} -eq 1 ]]; then
  echo "[FAIL] Potential out-of-scope path references found."
else
  echo "[PASS] No out-of-scope path references found."
fi
echo

echo "[4/6] SKILL.md frontmatter and section check"
if command -v rg >/dev/null 2>&1; then
  rg -n "^---$|^name:|^description:" "${skill_md}" || true
else
  grep -nE "^---$|^name:|^description:" "${skill_md}" || true
fi

meta_issue=0
if ! grep -q "^name:" "${skill_md}"; then
  echo "[FAIL] Missing 'name:' in ${skill_md}"
  meta_issue=1
fi
if ! grep -q "^description:" "${skill_md}"; then
  echo "[FAIL] Missing 'description:' in ${skill_md}"
  meta_issue=1
fi

if [[ ${meta_issue} -eq 0 ]]; then
  echo "[PASS] Frontmatter required fields detected."
fi

required_sections=(
  "## 输入前置清单"
  "## 执行流程"
)

for section in "${required_sections[@]}"; do
  if ! grep -Fq "${section}" "${skill_md}"; then
    echo "[FAIL] Missing required section in ${skill_md}: ${section}"
    meta_issue=1
  fi
done

if command -v rg >/dev/null 2>&1; then
  if ! rg -q "验收|验证" "${skill_md}"; then
    echo "[FAIL] Missing acceptance/verification hints in ${skill_md} (expected '验收' or '验证')."
    meta_issue=1
  fi
else
  if ! grep -Eq "验收|验证" "${skill_md}"; then
    echo "[FAIL] Missing acceptance/verification hints in ${skill_md} (expected '验收' or '验证')."
    meta_issue=1
  fi
fi

if [[ ${meta_issue} -eq 0 ]]; then
  echo "[PASS] SKILL.md section baseline looks good."
fi
echo

echo "[5/6] Official docs index consistency check"
index_issue=0
if [[ ! -f "${index_file}" ]]; then
  echo "[FAIL] Missing index file: ${index_file}"
  index_issue=1
else
  if command -v rg >/dev/null 2>&1; then
    listed_paths="$(rg -o "references/official-docs/[A-Za-z0-9._-]+\\.md" "${index_file}" | sort -u)"
  else
    listed_paths="$(grep -oE "references/official-docs/[A-Za-z0-9._-]+\\.md" "${index_file}" | sort -u)"
  fi

  if [[ -z "${listed_paths}" ]]; then
    echo "[FAIL] No official docs paths found in ${index_file}"
    index_issue=1
  fi

  while IFS= read -r path; do
    [[ -z "${path}" ]] && continue
    if [[ ! -f "${skill_dir}/${path}" ]]; then
      echo "[FAIL] Indexed official doc missing on disk: ${skill_dir}/${path}"
      index_issue=1
    fi
  done <<< "${listed_paths}"

  if [[ -d "${official_docs_dir}" ]]; then
    while IFS= read -r doc_file; do
      rel_path="references/official-docs/$(basename "${doc_file}")"
      if ! grep -Fq "${rel_path}" "${index_file}"; then
        echo "[FAIL] Official doc not registered in index: ${rel_path}"
        index_issue=1
      fi
    done < <(find "${official_docs_dir}" -maxdepth 1 -type f -name "*.md" | sort)
  fi
fi

if [[ ${index_issue} -eq 0 ]]; then
  echo "[PASS] Official docs index and files are consistent."
fi
echo

echo "[6/6] Interface reference code example check"
code_issue=0
found_interface_ref=0

while IFS= read -r ref_file; do
  found_interface_ref=1
  if ! grep -Fq '```' "${ref_file}"; then
    echo "[FAIL] Missing fenced code example in ${ref_file}"
    code_issue=1
  fi
done < <(
  find "${refs_dir}" -maxdepth 1 -type f -name "*.md" \
    ! -name "skill-template.md" \
    ! -name "reference-template.md" \
    ! -name "quality-checklist.md" \
    ! -name "official-docs-fallback.md" \
    | sort
)

if [[ ${found_interface_ref} -eq 0 ]]; then
  echo "[WARN] No top-level interface reference files found under ${refs_dir}."
elif [[ ${code_issue} -eq 0 ]]; then
  echo "[PASS] Interface references include fenced code examples."
fi
echo

if [[ ${structure_issue} -eq 1 || ${path_issue} -eq 1 || ${meta_issue} -eq 1 || ${index_issue} -eq 1 || ${code_issue} -eq 1 ]]; then
  echo "Quick check failed."
  exit 1
fi

echo "Quick check passed."
