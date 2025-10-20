--[[
================================================================================
UNIVERSAL COPY PASTE PLUGIN FOR YAZI
================================================================================



USAGE:
- Copy: proper_copy_paste copy [notify]
- Paste: plugin proper_copy_paste paste [notify]

[notify] is optional and will show a notification when the action is successful or failed.

              ┌─────────────────┐
              │ M:paste_entry() │
              └─────────────────┘
                      │
                      ∨
              ┌─────────────────┐           ┌─────────────────┐
              │  check_cut() │           │                 │
              │                 │           │                 │
              │  Does yazi has  │    YES    │   Paste using   │
              │  cut files in   │──────────>│   native yazi   │
              │  app state?     │           │   command       │
              │                 │           │                 │
              │                 │           │       END       │
              └─────────────────┘           └─────────────────┘
                      │
                NO    │
                      ∨
              ┌─────────────────┐           ┌─────────────────┐
           get_clipboard_file_uris()      handle_file_list_paste()
              │                 │           │                 │
              │ Able to extract │    YES    │ Check for       │
              │ file list from  │──────────>│ collisions and  │
              │ text/uri-list   │           │ paste using fs  │
              │ or              │           │                 │
              │ code/file-list? │           │       END       │
              └─────────────────┘           └─────────────────┘
                      │
                NO    │
                      ∨
              ┌─────────────────┐           ┌─────────────────┐
         get_clipboard_image_targets()      handle_image_paste()
              │                 │           │                 │
              │  Clipboard has  │    YES    │ Determines image│
              │  mimetype       │──────────>│ format and      │
              │  image/*?       │           │ assigns timestamp
              │                 │           │ before pasting  │
              │                 │           │ to file     END │
              └─────────────────┘           └─────────────────┘
                      │
                NO    │
                      ∨
              ┌─────────────────┐           ┌─────────────────┐
              │                 │           handle_text_paste()
              │                 │           │                 │
              │  Clipboard has  │    YES    │ Suggest pasting │
              │  any text?      │──────────>│ into new file   │
              │                 │           │                 │
              │                 │           │                 │
              │                 │           │       END       │
              └─────────────────┘           └─────────────────┘
                      │
                NO    │
                      ∨
              ┌─────────────────┐
              │ Display:        │
              │ Clipboard does  │
              │ not contain any │
              │ supported       │
              │ mimetypes  END  │
              └─────────────────┘

================================================================================
]]

local M = {}
local PackageName = "ucp"

---@enum STATE
local STATE = {
	INPUT_POSITION = "input_position",
	OVERWRITE_CONFIRM_POSITION = "overwrite_confirm_position",
	HIDE_NOTIFY = "hide_notify",
}

local set_state = ya.sync(function(state, key, value)
	if state then
		state[key] = value
	else
		state = {}
		state[key] = value
	end
end)

local get_state = ya.sync(function(state, key)
	if state then
		return state[key]
	else
		return nil
	end
end)
local function warn(s, ...)
	if get_state(STATE.HIDE_NOTIFY) then
		return
	end
	ya.notify({ title = PackageName, content = string.format(s, ...), timeout = 3, level = "warn" })
end

local function pathJoin(...)
	-- Detect OS path separator ('\' for Windows, '/' for Unix)
	local separator = package.config:sub(1, 1)
	local parts = { ... }
	local filteredParts = {}
	-- Remove empty strings or nil values
	for _, part in ipairs(parts) do
		if part and part ~= "" then
			table.insert(filteredParts, part)
		end
	end
	-- Join the remaining parts with the separator
	local path = table.concat(filteredParts, separator)
	-- Normalize any double separators (e.g., "folder//file" → "folder/file")
	path = path:gsub(separator .. "+", separator)

	return path
end

local get_cwd = ya.sync(function()
	return tostring(cx.active.current.cwd)
end)

local get_current_tab_id = ya.sync(function()
	return tostring(cx.active.id.value)
end)

local function input_file_name(default_name)
	local pos = get_state(STATE.INPUT_POSITION)
	pos = pos or { "center", w = 70 }

	local input_value, input_event = ya.input({
		title = "Paste into a new file. Enter file name:",
		value = default_name or "",
		pos = pos,
		-- TODO: remove this after next yazi released
		position = pos,
	})
	if input_event == 1 then
		if not input_value or input_value == "" then
			warn("File name can't be empty!")
			return
		elseif input_value:match("/$") then
			warn("File name can't ends with '/'")
			return
		end
		return input_value
	end
end

-- Detect if clipboard contains file URIs and extract all paths
local function get_clipboard_file_uris()
	-- Try macOS pbpaste first
	local handle = io.popen("pbpaste 2>/dev/null")
	if handle then
		local content = handle:read("*a")
		handle:close()

		if content and content:match("^/") then
			-- macOS pbpaste returns file paths directly when files are copied
			local file_paths = {}
			for file_path in content:gmatch("[^\r\n]+") do
				-- Only include absolute paths (starting with /)
				if file_path:match("^/") then
					table.insert(file_paths, file_path)
				end
			end
			if #file_paths > 0 then
				return file_paths
			end
		end
	end

	-- Try to get file URI from clipboard
	handle = io.popen("xclip -selection clipboard -o -t TARGETS 2>/dev/null || wl-paste --list-types 2>/dev/null")
	if not handle then
		return nil
	end

	local targets = handle:read("*a")
	handle:close()

	-- Debug: Print available targets
	ya.dbg("Available clipboard targets: %s", targets or "none")

	-- Check for code/file-list target for compatibility with vscode-like editors
	if targets:match("code/file%-list") then
		ya.dbg("Found code/file-list target, attempting to read...")
		-- Try code/file-list format (VS Code and other editors)
		local code_handle = io.popen("xclip -selection clipboard -o -t code/file-list 2>/dev/null")
		if code_handle then
			local code_content = code_handle:read("*a")
			code_handle:close()

			ya.dbg("code/file-list content: %s", code_content or "empty")

			local file_paths = {}
			for file_path in code_content:gmatch("[^\r\n]+") do
				-- Handle file:// URIs
				if file_path:match("^file://") then
					-- Extract path from file:// URI and URL decode
					local path = file_path:gsub("^file://", "")
					path = path:gsub("%%(%x%x)", function(hex)
						return string.char(tonumber(hex, 16))
					end)
					table.insert(file_paths, path)
					-- Handle direct paths
				elseif file_path:match("^/") or file_path:match("^%.%/") then
					table.insert(file_paths, file_path)
				end
			end
			if #file_paths > 0 then
				ya.dbg("Found %d file paths in code/file-list", #file_paths)
				return file_paths
			end
		end
	else
		ya.dbg("code/file-list target not found in clipboard")

		-- Fallback to text/uri-list if code/file-list is not available
		if targets:match("text/uri%-list") then
			ya.dbg("Found text/uri-list target as fallback, attempting to read...")
			local uri_handle = io.popen(
				"xclip -selection clipboard -o -t text/uri-list 2>/dev/null || wl-paste -t text/uri-list 2>/dev/null")
			if uri_handle then
				local uri_content = uri_handle:read("*a")
				uri_handle:close()

				ya.dbg("text/uri-list content: %s", uri_content or "empty")

				local file_paths = {}
				for file_path in uri_content:gmatch("file://([^\r\n]+)") do
					-- URL decode the path
					file_path = file_path:gsub("%%(%x%x)", function(hex)
						return string.char(tonumber(hex, 16))
					end)
					table.insert(file_paths, file_path)
				end
				if #file_paths > 0 then
					ya.dbg("Found %d file paths in text/uri-list", #file_paths)
					return file_paths
				end
			end
		end
	end

	return nil
end

-- Get all available image formats from clipboard
local function get_clipboard_image_targets()
	-- Try macOS first
	local handle = io.popen("osascript -e 'return (clipboard info) as string' 2>/dev/null")
	if handle then
		local info = handle:read("*a")
		handle:close()

		if info and info:match("picture") then
			-- macOS has image in clipboard, return a generic image target
			-- We'll get the actual format in handle_image_paste()
			return "image/png image/jpeg image/tiff image/gif"
		end
	end

	-- Try Linux clipboard tools
	handle = io.popen("xclip -selection clipboard -o -t TARGETS 2>/dev/null || wl-paste --list-types 2>/dev/null")
	if not handle then
		return nil
	end

	local targets = handle:read("*a")
	handle:close()

	if targets and targets:match("image/") then
		return targets
	end

	return nil
end

-- Detect best image format from clipboard (with priority)
local function get_best_image_format(targets)
	if not targets then return nil end

	-- svg differs from pattern image/jpeg -> .jpeg
	if targets:match("image/svg%+xml") then
		return "svg"
		-- common pattern image/jpeg -> .jpeg
	elseif targets:match("image/") then
		local format = targets:match("image/([%w-]+)")
		if format then
			return format
		else
			-- fallback to png
			warn("Could not determine image format!")
			return "png"
		end
	end

	-- For macOS, if we detect an image but can't determine format, default to png
	if targets:match("image/png image/jpeg image/tiff image/gif") then
		return "png"
	end

	return nil
end

-- Get image data from clipboard
local function get_clipboard_image_data(format)
	local mime_type = "image/" .. format
	if format == "svg" then
		mime_type = "image/svg+xml"
	end

	-- Try macOS clipboard first
	local handle = io.popen("osascript -e 'return (clipboard info) as string' 2>/dev/null")
	if handle then
		local info = handle:read("*a")
		handle:close()

		if info and info:match("picture") then
			-- macOS has image in clipboard, save it to a temporary file and read it
			local temp_file = "/tmp/yazi_clipboard_image." .. format
			local save_cmd = string.format(
				"osascript -e 'set the clipboard to (read (POSIX file \"%s\") as «class PNGf»)' 2>/dev/null || osascript -e 'set the clipboard to (read (POSIX file \"%s\") as «class JPEG»)' 2>/dev/null",
				temp_file, temp_file)

			-- First, try to get the image data using pbpaste with different formats
			local pbpaste_cmd =
			"pbpaste -Prefer png 2>/dev/null || pbpaste -Prefer jpeg 2>/dev/null || pbpaste 2>/dev/null"
			handle = io.popen(pbpaste_cmd)
			if handle then
				local data = handle:read("*a")
				handle:close()
				if data and #data > 0 then
					return data
				end
			end
		end
	end

	-- Try X11 clipboard (xclip)
	handle = io.popen(string.format("xclip -selection clipboard -o -t %s 2>/dev/null", mime_type))
	if handle then
		local data = handle:read("*a")
		handle:close()
		if data and #data > 0 then
			return data
		end
	end

	-- Try Wayland clipboard (wl-paste)
	handle = io.popen(string.format("wl-paste -t %s 2>/dev/null", mime_type))
	if handle then
		local data = handle:read("*a")
		handle:close()
		if data and #data > 0 then
			return data
		end
	end

	return nil
end

-- Extract filename from a full path
local function get_filename_from_path(path)
	-- Remove trailing slashes
	path = path:gsub("/$", "")
	-- Extract filename (everything after the last separator)
	local separator = package.config:sub(1, 1)
	local filename = path:match("[^" .. separator .. "]+$")
	return filename or path
end

function M:setup(opts)
	if opts and opts.hide_notify and type(opts.hide_notify) == "boolean" then
		set_state(STATE.HIDE_NOTIFY, opts.hide_notify)
	else
		set_state(STATE.HIDE_NOTIFY, false)
	end
	if opts and opts.input_position and type(opts.input_position) == "table" then
		set_state(STATE.INPUT_POSITION, opts.input_position)
	else
		set_state(STATE.INPUT_POSITION, { "center", w = 70 })
	end
	if opts and opts.overwrite_confirm_position and type(opts.overwrite_confirm_position) == "table" then
		set_state(STATE.OVERWRITE_CONFIRM_POSITION, opts.overwrite_confirm_position)
	else
		set_state(STATE.OVERWRITE_CONFIRM_POSITION, { "center", w = 70, h = 10 })
	end
end

function M:entry(job)
	local action = job.args[1]

	-- Debug: Show what action we received
	ya.dbg("Action: '%s', Args: %s", tostring(action), table.concat(job.args or {}, ", "))

	if not action then
		-- Default to paste behavior for backward compatibility
		ya.dbg("No action, defaulting to paste")
		return M:paste_entry(job)
	end

	if action == "copy" then
		ya.dbg("Calling copy_entry")
		return M:copy_entry(job)
	elseif action == "paste" then
		ya.dbg("Calling paste_entry")
		return M:paste_entry(job)
	else
		-- Default to paste behavior for backward compatibility
		ya.dbg("Unknown action '%s', defaulting to paste", tostring(action))
		return M:paste_entry(job)
	end
end

local check_cut = ya.sync(function(_)
	return #cx.yanked > 0 and cx.yanked.is_cut
end)


function M:paste_entry(job)
	local no_hover = job.args.no_hover == nil and false or job.args.no_hover
	local show_notify = false
	for _, arg in ipairs(job.args or {}) do
		if arg == "notify" then
			show_notify = true
			break
		end
	end

	-- 1: Check if there are cut files in the app state
	local has_yanked = check_cut(job.args[1])
	if has_yanked then
		-- If there are cut files, paste them using native command
		ya.emit("paste", {})
		return
	end

	-- 2: Handle text/uri-list and code/file-list clipboard mimetype
	local file_uris = get_clipboard_file_uris()
	if file_uris and #file_uris > 0 then
		M:handle_file_list_paste(file_uris, no_hover, show_notify)
		return
	end

	-- 3: Handle image/* mimetype
	local image_targets = get_clipboard_image_targets()
	if image_targets then
		M:handle_image_paste(image_targets, no_hover, show_notify)
		return
	end

	-- 4: Handle text/plain mimetype - suggest creating a new file
	local clipboard_content = ya.clipboard()
	if clipboard_content and clipboard_content ~= "" then
		M:handle_text_paste(clipboard_content, no_hover, show_notify)
		return
	end

	-- None of the supported mimetypes are present
	warn("Clipboard does not contain any supported mimetypes")
end

-- Handle file list paste (code/file-list mimetype)
function M:handle_file_list_paste(file_uris, no_hover, show_notify)
	local success_count = 0

	-- If set, will automatically apply the action to all files
	local bulk_action = nil -- nil, "overwrite_all", "copy_all", "cancel_all"

	for i, file_uri in ipairs(file_uris) do
		-- Extract filename from the URI path
		local file_name = get_filename_from_path(file_uri)

		-- Read the actual file content
		local source_file = io.open(file_uri, "rb")
		if source_file then
			local file_content = source_file:read("*a")
			source_file:close()

			-- Save the file
			local file_path = Url(pathJoin(get_cwd(), file_name))
			local cha, _ = fs.cha(file_path)
			if cha then
				-- File exists, ask user what to do (unless bulk action is set)
				local action = nil

				if bulk_action == "cancel_all" then
					-- Skip all remaining files
					break
				elseif bulk_action == "overwrite_all" then
					action = 1 -- Overwrite
				elseif bulk_action == "copy_all" then
					action = 2 -- Create copy
				else
					-- Show bottom menu with file info in title
					action = ya.which({
						cands = {
							{ desc = string.format("File: %s", file_name),            on = "|" },
							{ desc = string.format("Progress: %d/%d", i, #file_uris), on = "|" },
							{ desc = "",                                              on = "|" },
							{ desc = "Overwrite",                                     on = "o" },
							{ desc = "Create copy (_copy)",                           on = "c" },
							{ desc = "Skip file",                                     on = "q" },
							{ desc = "Overwrite All",                                 on = "O" },
							{ desc = "Create copy All",                               on = "C" },
							{ desc = "Cancel All",                                    on = "Q" }
						}
					})

					-- Adjust action index since first three items are info only
					if action and action > 3 then
						action = action - 3
					elseif action and action <= 3 then
						action = nil
					end

					-- Set bulk actions is selected
					if action == 4 then
						bulk_action = "overwrite_all"
						action = 1
					elseif action == 5 then
						bulk_action = "copy_all"
						action = 2
					elseif action == 6 then
						bulk_action = "cancel_all"
						break
					end
				end

				if action == 1 then -- Overwrite
					local deleted_collided_item, _ = fs.remove("file", file_path)
					if not deleted_collided_item then
						warn("Failed to delete collided file: %s", tostring(file_path))
						return
					end
					if file_path.parent then
						fs.create("dir_all", file_path.parent)
					end
					fs.write(file_path, file_content)
					success_count = success_count + 1
					if not no_hover then
						ya.emit("reveal",
							{ tostring(file_path), tab = get_current_tab_id(), no_dummy = true, raw = true })
					end
				elseif action == 2 then -- Create copy
					-- Generate copy filename with simple _copy suffix
					local copy_name = file_name:gsub("(%.%w+)$", "_copy%1")
					if not copy_name:match("_copy") then
						copy_name = copy_name .. "_copy"
					end
					local copy_path = Url(pathJoin(get_cwd(), copy_name))

					if copy_path.parent then
						fs.create("dir_all", copy_path.parent)
					end
					fs.write(copy_path, file_content)
					success_count = success_count + 1
					if not no_hover then
						ya.emit("reveal",
							{ tostring(copy_path), tab = get_current_tab_id(), no_dummy = true, raw = true })
					end
				end
				-- If action == 3 (Cancel) or nil, do nothing
			else
				if file_path.parent then
					fs.create("dir_all", file_path.parent)
				end
				fs.write(file_path, file_content)
				success_count = success_count + 1
				if not no_hover then
					ya.emit("reveal", { tostring(file_path), tab = get_current_tab_id(), no_dummy = true, raw = true })
				end
			end
		else
			warn("Failed to read file: %s", file_uri)
		end
	end

	if success_count > 0 and show_notify then
		ya.notify({
			title = PackageName,
			content = string.format("Successfully pasted %d file(s)", success_count),
			timeout = 3,
			level = "info"
		})
	end

	-- Remove yank highlight after pasting
	ya.manager_emit("unyank", {})
end

-- Handle image paste (image/ mimetype)
function M:handle_image_paste(image_targets, no_hover, show_notify)
	-- Get best format as suggestion
	local suggested_format = get_best_image_format(image_targets)
	if not suggested_format then
		ya.err("Could not determine image format!")
		return
	end

	-- Pasted image filename
	local timestamp = os.date("%Y-%m-%d_%H-%M-%S")
	local file_name = string.format("pasted_%s.%s", timestamp, suggested_format)

	-- Get the image data from clipboard
	local clipboard_content = get_clipboard_image_data(suggested_format)
	if not clipboard_content or #clipboard_content == 0 then
		ya.err("Failed to get image data from clipboard!")
		return
	end

	M:save_file_with_conflict_handling(file_name, clipboard_content, no_hover, show_notify)
end

-- Handle text paste (text/plain mimetype)
function M:handle_text_paste(clipboard_content, no_hover, show_notify)
	-- Prompt user for filename
	local file_name = input_file_name()
	if not file_name or file_name == "" then
		return
	end

	M:save_file_with_conflict_handling(file_name, clipboard_content, no_hover, show_notify)
end

function M:save_file_with_conflict_handling(file_name, clipboard_content, no_hover, show_notify)
	-- Save the file
	local file_path = Url(pathJoin(get_cwd(), file_name))
	local cha, _ = fs.cha(file_path)
	if cha then
		-- If file exists, ask user what to do
		local pos = get_state(STATE.OVERWRITE_CONFIRM_POSITION)
		pos = pos or { "center", w = 80, h = 20 }

		-- Show bottom menu with file info as menu items
		local action = ya.which({
			cands = {
				{ desc = string.format("File: %s", file_name), on = "|" },
				{ desc = "",                                   on = "|" },
				{ desc = "",                                   on = "|" },
				{ desc = "Overwrite",                          on = "o" },
				{ desc = "Create copy (_copy)",                on = "c" },
				{ desc = "Cancel",                             on = "q" },
			}
		})

		-- Adjust action index since first three items are info only
		if action and action > 3 then
			action = action - 3
		elseif action and action <= 3 then
			action = nil -- Info items are not selectable
		end

		if action == 1 then -- Overwrite
			local deleted_collided_item, _ = fs.remove("file", file_path)
			if not deleted_collided_item then
				warn("Failed to delete collided file: %s", tostring(file_path))
				return
			end
			if file_path.parent then
				fs.create("dir_all", file_path.parent)
			end
			fs.write(file_path, clipboard_content)
			if not no_hover then
				ya.emit("reveal", { tostring(file_path), tab = get_current_tab_id(), no_dummy = true, raw = true })
			end
		elseif action == 2 then -- Create copy
			local copy_name = file_name:gsub("(%.%w+)$", "_copy%1")
			if not copy_name:match("_copy") then
				copy_name = copy_name .. "_copy"
			end
			local copy_path = Url(pathJoin(get_cwd(), copy_name))

			if copy_path.parent then
				fs.create("dir_all", copy_path.parent)
			end
			fs.write(copy_path, clipboard_content)
			if not no_hover then
				ya.emit("reveal", { tostring(copy_path), tab = get_current_tab_id(), no_dummy = true, raw = true })
			end
		end
		-- If action == 3 (Cancel) or nil, do nothing
	else
		if file_path.parent then
			fs.create("dir_all", file_path.parent)
		end
		fs.write(file_path, clipboard_content)
		if not no_hover then
			ya.emit("reveal", { tostring(file_path), tab = get_current_tab_id(), no_dummy = true, raw = true })
		end
	end

	-- Remove yank highlight after pasting
	ya.manager_emit("unyank", {})
end

-- Get selected or hovered files using ya.sync
local selected_or_hovered = ya.sync(function()
	local tab, paths = cx.active, {}
	for _, u in pairs(tab.selected) do
		paths[#paths + 1] = tostring(u)
	end
	if #paths == 0 and tab.current.hovered then
		paths[1] = tostring(tab.current.hovered.url)
	end
	return paths
end)

-- Copy entry function for copying selected files to clipboard
function M:copy_entry(job)
	ya.dbg("copy_entry called")

	local show_notify = false
	for _, arg in ipairs(job.args or {}) do
		if arg == "notify" then
			show_notify = true
			break
		end
	end

    --support for visual mode, we call it escape before selected_or_hovered, so all files become available in tab.selected
    ya.emit("escape", { visual = true })

	-- Get selected or hovered files first
	local urls = selected_or_hovered()
	ya.dbg("urls: %s", table.concat(urls, ", "))
	ya.dbg("urls length: %d", #urls)

	if #urls == 0 then
		if show_notify then
			ya.notify({ title = PackageName, content = "No file selected", level = "warn", timeout = 5 })
		end
		return
	end

	-- Call yank to highlight selected files
	ya.emit("yank", {})

	-- Format the URLs for `text/uri-list` specification
	local function encode_uri(uri)
		return uri:gsub("([^%w%-%._~:/])", function(c)
			return string.format("%%%02X", string.byte(c))
		end)
	end

	local file_list_formatted = ""
	for _, path in ipairs(urls) do
		-- Each file path must be URI-encoded and prefixed with "file://"
		file_list_formatted = file_list_formatted .. "file://" .. encode_uri(path) .. "\r\n"
	end

	ya.dbg("file_list_formatted: %s", file_list_formatted)

	-- Try different clipboard commands based on platform
	local status, err = nil, nil

	-- Try wl-copy first (Wayland)
	ya.dbg("Attempting wl-copy with text/uri-list target...")
	status, err = Command("wl-copy"):arg("--type"):arg("text/uri-list"):arg(file_list_formatted):spawn():wait()
	ya.dbg("wl-copy text/uri-list result: status=%s, err=%s", status and tostring(status.success) or "nil", err or "nil")

	-- If wl-copy fails, try pbcopy (macOS)
	if not status or not status.success then
		ya.dbg("wl-copy failed, trying pbcopy...")
		status, err = Command("pbcopy"):arg(file_list_formatted):spawn():wait()
		ya.dbg("pbcopy result: status=%s, err=%s", status and tostring(status.success) or "nil", err or "nil")
	end

	-- If both fail, try xclip (X11)
	if not status or not status.success then
		ya.dbg("pbcopy failed, trying xclip...")
		status, err = Command("xclip"):arg("-selection"):arg("clipboard"):arg("-t"):arg("text/uri-list"):arg(file_list_formatted):spawn():wait()
		ya.dbg("xclip result: status=%s, err=%s", status and tostring(status.success) or "nil", err or "nil")
	end

	if show_notify then
		if status and status.success then
			ya.notify({
				title = PackageName,
				content = "Successfully copied the file(s) to system clipboard",
				level = "info",
				timeout = 5,
			})
		else
			ya.notify({
				title = PackageName,
				content = string.format("Could not copy selected file(s) %s", status and status.code or err),
				level = "error",
				timeout = 5,
			})
		end
	end
end

return M
