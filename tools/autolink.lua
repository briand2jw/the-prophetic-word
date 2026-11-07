-- Links the first mention of each section name or Bible book on a page to its page.
-- English pages only. Names inside italics or quotation marks (article titles) are not linked. To keep a name from being linked, wrap it: [Daniel]{.no-autolink}
local NAMES = {
  {"Know the Truth", "know-the-truth"},
  {"The Prophetic Word", "prophetic-word"},
  {"Seek Jehovah", "seek-jehovah"},
  {"Daniel", "daniel"},
  {"Matthew", "matthew"},
  {"Revelation", "revelation"},
}
local SKIP_CLASS = {["section-list"]=true, ["chapter-grid"]=true, ["article-list"]=true,
  ["meta"]=true, ["no-autolink"]=true, ["epub-btn"]=true, ["translate-btn"]=true,
  ["box-title"]=true, ["epub-note"]=true}

local input = (PANDOC_STATE.input_files or {})[1] or ""
-- Quarto knows the real page path and project folder; use them when available
if quarto and quarto.doc and quarto.doc.input_file and quarto.project and quarto.project.directory then
  local full, root = tostring(quarto.doc.input_file), tostring(quarto.project.directory)
  if full:sub(1, #root) == root then input = full:sub(#root + 2) end
end
input = input:gsub("^%./", "")
local depth = select(2, input:gsub("/", ""))
local prefix = string.rep("../", depth)
local self_page = input:gsub("%.qmd$", "")
local used = {}
local english = true
local targets = {}

local function exists(slug)
  -- Quarto runs from the project folder; also accept the page's own folder
  for _, p in ipairs({slug .. ".qmd", prefix .. slug .. ".qmd"}) do
    local f = io.open(p, "r")
    if f then f:close() return true end
  end
  return false
end

local function skipped(el)
  for _, c in ipairs(el.classes or {}) do if SKIP_CLASS[c] then return true end end
  return false
end

local function split(s)
  local lead, core, trail = s:match("^([%p]*)(.-)([%p]*)$")
  -- keep a possessive ending with the word
  for _, suffix in ipairs({"’s", "'s"}) do
    if #core > #suffix and core:sub(-#suffix) == suffix then
      core, trail = core:sub(1, -#suffix - 1), suffix .. trail
      break
    end
  end
  return lead, core, trail
end

local function plain_words(name)
  local t = {}
  for w in name:gmatch("%S+") do t[#t+1] = w end
  return t
end

local function process(inls)
  local out, i, n = pandoc.List(), 1, #inls
  while i <= n do
    local el, hit = inls[i], nil
    if el.t == "Str" then
      for _, nm in ipairs(NAMES) do
        local slug = nm[2]
        if not used[slug] and slug ~= self_page and targets[slug] then
          local words = plain_words(nm[1])
          local ok, j, lead, trail = true, i, "", ""
          for k, w in ipairs(words) do
            local s = inls[j]
            if not (s and s.t == "Str") then ok = false break end
            local l, core, t = split(s.text)
            if core ~= w then ok = false break end
            if k == 1 then lead = l elseif l ~= "" then ok = false break end
            if k < #words then
              if t ~= "" or not (inls[j+1] and inls[j+1].t == "Space") then ok = false break end
              j = j + 2
            else trail = t end
          end
          -- skip scripture references such as "Daniel 7:13"
          if ok and inls[j+1] and inls[j+1].t == "Space" and inls[j+2] and inls[j+2].t == "Str"
             and inls[j+2].text:match("^%d") then ok = false end
          if ok then
            hit = {slug = slug, name = nm[1], last = j, lead = lead, trail = trail}
            break
          end
        end
      end
    end
    if hit then
      used[hit.slug] = true
      if hit.lead ~= "" then out:insert(pandoc.Str(hit.lead)) end
      local label = pandoc.List()
      for k, w in ipairs(plain_words(hit.name)) do
        if k > 1 then label:insert(pandoc.Space()) end
        label:insert(pandoc.Str(w))
      end
      out:insert(pandoc.Link(label, prefix .. hit.slug .. ".qmd", "",
        pandoc.Attr("", {"autolink"})))
      if hit.trail ~= "" then out:insert(pandoc.Str(hit.trail)) end
      i = hit.last + 1
    else
      out:insert(el)
      i = i + 1
    end
  end
  return out
end

if FORMAT:match("html") then
  for _, nm in ipairs(NAMES) do targets[nm[2]] = exists(nm[2]) end
  return {{
    Meta = function(m)
      local lang = m.lang and pandoc.utils.stringify(m.lang) or "en"
      english = lang:match("^en") ~= nil
    end,
  }, {
    traverse = "topdown",
    Header = function() return nil, false end,
    Link = function() return nil, false end,
    Code = function() return nil, false end,
    -- article titles are written in italics or quotation marks
    Emph = function() return nil, false end,
    Quoted = function() return nil, false end,
    CodeBlock = function() return nil, false end,
    Div = function(d) if skipped(d) then return nil, false end end,
    Span = function(s) if skipped(s) then return nil, false end end,
    Inlines = function(inls) if english then return process(inls) end end,
  }}
else
  return {}
end
