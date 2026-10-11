"""Modal dialogs."""

from __future__ import annotations

from rich.markup import escape
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList, Static, Tree
from textual.widgets.option_list import Option

from ..core import deploy, nexus, pathops, report
from ..core.library import Library
from ..core.models import OP_REMOVE, OP_RENAME, Mod, PathOp
from ..core.nexus import NexusClient, NexusError, NxmLink

HIDDEN_CATEGORIES = {"ARCHIVED", "REMOVED"}


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [
        Binding("y,enter", "answer(True)", "Yes"),
        Binding("n,escape", "answer(False)", "No"),
    ]

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Static(self.message, markup=False)
            yield Label("[b]y[/] yes   [b]n[/] no", classes="hint")

    def action_answer(self, value: bool) -> None:
        self.dismiss(value)


class TextPromptScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, value: str = "", placeholder: str = "") -> None:
        super().__init__()
        self.title_text = title
        self.value = value
        self.placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_text, classes="title", markup=False)
            yield Input(value=self.value, placeholder=self.placeholder)
            yield Label("[b]enter[/] ok   [b]esc[/] cancel", classes="hint")

    @on(Input.Submitted)
    def submit(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    def action_cancel(self) -> None:
        self.dismiss(None)


class GroupScreen(ModalScreen[str | None]):
    """Pick one of the existing groups or type a new one. Result: the group name."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("down", "to_list", "Existing groups", show=False),
    ]

    def __init__(self, current: str, groups: list[str]) -> None:
        super().__init__()
        self.current = current
        self.groups = groups

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label("Group (empty for none)", classes="title")
            yield Input(value=self.current, placeholder="type a new group or pick one below")
            yield OptionList(id="groups")
            yield Label(
                "[b]enter[/] ok   [b]\u2193[/] existing groups   [b]esc[/] cancel", classes="hint"
            )

    def on_mount(self) -> None:
        self._show(self.groups)

    def _show(self, groups: list[str]) -> None:
        options = self.query_one("#groups", OptionList)
        options.clear_options()
        options.add_options([Option(escape(group), id=group) for group in groups])
        options.display = bool(groups)

    @on(Input.Changed)
    def narrow(self, event: Input.Changed) -> None:
        """Typing narrows the list down; the unchanged current group shows all of them."""
        text = event.value.strip().casefold()
        if text == self.current.casefold():
            self._show(self.groups)
        else:
            self._show([group for group in self.groups if text in group.casefold()])

    def action_to_list(self) -> None:
        options = self.query_one("#groups", OptionList)
        if options.display and not options.has_focus:
            options.highlighted = 0
            options.focus()

    @on(Input.Submitted)
    def submit(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip())

    @on(OptionList.OptionSelected)
    def select(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ToolsScreen(ModalScreen[str | None]):
    """A menu of one-off tools. Result: the id of the chosen tool."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, tools: list[tuple[str, str]]) -> None:
        super().__init__()
        self.tools = tools

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label("Tools", classes="title")
            yield OptionList(*(Option(escape(label), id=tool) for tool, label in self.tools))
            yield Label("[b]enter[/] run   [b]esc[/] cancel", classes="hint")

    @on(OptionList.OptionSelected)
    def select(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class NexusAddScreen(ModalScreen[tuple[int, int, NxmLink | None] | None]):
    """Pick a file of a Nexus mod. Result: `(mod_id, file_id, nxm link or None)`."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, client: NexusClient, premium: bool) -> None:
        super().__init__()
        self.client = client
        self.premium = premium
        self.mod_id: int | None = None

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide"):
            yield Label("Add mod from Nexus", classes="title")
            yield Input(placeholder="mod URL, mod id or nxm:// link")
            yield Static("", id="info", markup=False)
            yield OptionList(id="files")
            yield Label("[b]enter[/] look up / download   [b]esc[/] cancel", classes="hint")

    def on_mount(self) -> None:
        self.query_one("#files").display = False
        if not self.premium:
            self.query_one("#info", Static).update(
                "Free Nexus account: open the mod's Files tab in your browser, click "
                '"Mod Manager Download" and paste the nxm:// link here.'
            )

    @on(Input.Submitted)
    def submit(self, event: Input.Submitted) -> None:
        self.lookup(event.value.strip())

    @work(exclusive=True)
    async def lookup(self, text: str) -> None:
        info = self.query_one("#info", Static)
        try:
            if text.startswith("nxm:"):
                link = nexus.parse_nxm(text)
                if link.game != nexus.GAME:
                    raise NexusError(f"that link is for {link.game!r}, not {nexus.GAME!r}")
                self.dismiss((link.mod_id, link.file_id, link))
                return
            mod_id = nexus.parse_mod_ref(text)
            info.update("Looking up mod ...")
            mod_info = await self.client.mod(mod_id)
            listing = await self.client.files(mod_id)
        except NexusError as exc:
            info.update(str(exc))
            return
        files = [
            f for f in listing.get("files", []) if f.get("category_name") not in HIDDEN_CATEGORIES
        ]
        files.sort(key=lambda f: f.get("uploaded_timestamp") or 0, reverse=True)
        self.mod_id = mod_id
        hint = "" if self.premium else " - downloading needs an nxm:// link (free account)"
        info.update(f"{mod_info.get('name', mod_id)}: {len(files)} file(s){hint}")
        options = self.query_one("#files", OptionList)
        options.clear_options()
        for file in files:
            size_mb = (file.get("size_kb") or 0) / 1024
            label = (
                f"{file.get('name', '?')}  v{file.get('version') or '?'}  "
                f"[{file.get('category_name', '?')}]  {size_mb:.1f} MB"
            )
            options.add_option(Option(escape(label), id=str(file["file_id"])))
        options.display = True
        if files:
            options.highlighted = 0
            options.focus()

    @on(OptionList.OptionSelected)
    def select(self, event: OptionList.OptionSelected) -> None:
        if self.mod_id is not None and event.option.id:
            self.dismiss((self.mod_id, int(event.option.id), None))

    def action_cancel(self) -> None:
        self.dismiss(None)


class PathTree(Tree[str]):
    """A tree that folds with the arrow keys, like a file manager."""

    BINDINGS = [
        Binding("left", "fold", "Collapse", show=False),
        Binding("right", "unfold", "Expand", show=False),
    ]

    def action_fold(self) -> None:
        node = self.cursor_node
        if node is None:
            return
        if node.allow_expand and node.is_expanded and not node.is_root:
            node.collapse()
        elif node.parent is not None:
            self.move_cursor(node.parent)

    def action_unfold(self) -> None:
        node = self.cursor_node
        if node is not None and node.allow_expand and not node.is_expanded:
            node.expand()


class ConflictsScreen(ModalScreen[None]):
    """Conflict report: mods involved -> file -> every copy with its folder."""

    BINDINGS = [
        Binding("a", "accept", "Accept / un-accept"),
        Binding("escape,q,v", "close", "Close"),
    ]

    def __init__(self, library: Library) -> None:
        super().__init__()
        self.library = library

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide tall"):
            yield Label("Conflicts (the last mod wins)", classes="title")
            yield PathTree("Conflicts", id="conflicts")
            yield Label(
                "[b]a[/] accept as intended / take back / drop unused rule   "
                "[b]\u2190 \u2192[/] fold   [b]esc[/] close",
                classes="hint",
            )

    def on_mount(self) -> None:
        tree = self.query_one("#conflicts", PathTree)
        tree.show_root = False
        self.rebuild()
        tree.focus()

    def rebuild(self, select: str | None = None) -> None:
        plan = deploy.build_plan(self.library)
        tree = self.query_one("#conflicts", PathTree)
        tree.clear()
        nodes = {}
        for group in report.conflict_groups(self.library, plan):
            group_node = tree.root.add(
                escape(report.group_line(group)), data=("group", group), expand=True
            )
            nodes[group.title] = group_node
            for conflict in group.conflicts:
                line = report.file_line(self.library, conflict)
                file_node = group_node.add(escape(line), data=("file", conflict), expand=True)
                nodes[f"{group.title}/{conflict.name}"] = file_node
                for copy in report.provider_lines(self.library, conflict):
                    file_node.add_leaf(escape(copy))
        if merged := report.merged_lines(self.library, plan):
            merged_node = tree.root.add("Merged (no mod loses)", expand=True)
            for line in merged:
                merged_node.add_leaf(escape(line))
        if unused := report.unused_rules(self.library):
            unused_node = tree.root.add(report.UNUSED_TITLE, expand=True)
            nodes[report.UNUSED_TITLE] = unused_node
            for mod_id, rule in unused:
                line = report.unused_line(self.library, mod_id, rule)
                unused_node.add_leaf(escape(line), data=("unused", (mod_id, rule.mod)))
        if select in nodes:
            tree._tree_lines  # noqa: B018 - lays the new nodes out, so the cursor can go there
            tree.move_cursor(nodes[select])

    def action_accept(self) -> None:
        """Toggle acceptance: of a file, or (on a mods line) of everything between those mods."""
        node = self.query_one("#conflicts", PathTree).cursor_node
        if node is None or node.data is None:
            return
        kind, item = node.data
        if kind == "unused":  # a rule about a mod that is gone: `a` drops it
            self.library.remove_overwrite(item[0], [item[1]])
            self.rebuild(select=report.UNUSED_TITLE)
            return
        conflicts = item.conflicts if kind == "group" else [item]
        *losers, winner = conflicts[0].mod_ids
        if any(conflict.status == deploy.ERROR for conflict in conflicts):
            self.notify("Wrong order: move the mods, then accept", severity="warning")
            return
        if kind == "group":
            key = item.title
            if all(conflict.status == deploy.ACCEPTED for conflict in conflicts):
                self.library.remove_overwrite(winner, losers)
            else:
                self.library.accept_overwrite(winner, losers)
        else:
            key = f"{node.parent.data[1].title}/{item.name}"
            if item.status == deploy.OPEN:
                self.library.accept_overwrite(winner, losers, item.name)
            elif not self.library.remove_overwrite(winner, losers, item.name):
                self.notify("Accepted for the whole mod: take it back on the mods line")
                return
        self.rebuild(select=key)

    def action_close(self) -> None:
        self.dismiss(None)


class PathOpsScreen(ModalScreen[list[PathOp] | None]):
    """Edit the remove/rename operations of a mod on a preview of its target tree."""

    BINDINGS = [
        Binding("r,delete", "remove", "Remove"),
        Binding("m", "rename", "Rename/move"),
        Binding("u", "undo", "Undo last"),
        Binding("c", "toggle_all", "Collapse/expand all"),
        Binding("slash", "filter", "Filter"),
        Binding("s", "save", "Save"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, mod: Mod, base: dict[str, str]) -> None:
        super().__init__()
        self.mod = mod
        self.base = base
        self.ops = list(mod.ops)
        self.filter = ""
        self.collapsed: set[str] = set()  # folders the user folded, kept across rebuilds

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide tall"):
            yield Label(f"Paths of {self.mod.name}", classes="title", markup=False)
            yield PathTree("Dragon Age/", id="targets")
            yield Input(placeholder="filter by part of a file or folder name", id="filter")
            yield Static("", id="ops", markup=False)
            yield Label(
                "[b]r[/] remove   [b]m[/] rename/move   [b]u[/] undo last   [b]/[/] filter   "
                "[b]\u2190 \u2192[/] fold   [b]c[/] fold all   [b]s[/] save   [b]esc[/] cancel",
                classes="hint",
            )

    def on_mount(self) -> None:
        self.query_one("#filter").display = False
        self.rebuild()

    def _matches(self, target: str) -> bool:
        """A path is shown if the filter text occurs in one of its file or folder names."""
        return any(self.filter in part.casefold() for part in target.split("/"))

    def _remember_folds(self, tree: PathTree) -> None:
        if self.filter:
            return  # a filtered tree is shown fully expanded; keep the folds from before

        def walk(node) -> None:
            for child in node.children:
                if child.allow_expand:
                    if child.is_expanded:
                        self.collapsed.discard(child.data)
                    else:
                        self.collapsed.add(child.data)
                    walk(child)

        walk(tree.root)

    def rebuild(self, *, keep_folds: bool = True) -> None:
        tree = self.query_one("#targets", PathTree)
        selected = self._selected()
        if keep_folds:
            self._remember_folds(tree)
        tree.clear()
        tree.root.expand()
        nodes = {"": tree.root}
        for target in pathops.apply_ops(self.base, self.ops):
            if self.filter and not self._matches(target):
                continue
            parts = target.split("/")
            for depth in range(1, len(parts)):
                path = "/".join(parts[:depth])
                if path not in nodes:
                    parent = nodes["/".join(parts[: depth - 1])]
                    expand = bool(self.filter) or path not in self.collapsed
                    nodes[path] = parent.add(escape(parts[depth - 1]), data=path, expand=expand)
            nodes[target] = nodes["/".join(parts[:-1])].add_leaf(escape(parts[-1]), data=target)
        # put the cursor back on the same entry, or the closest folder that still exists
        while selected and selected not in nodes:
            selected = selected.rpartition("/")[0]
        if selected:
            tree._tree_lines  # noqa: B018 - lays the new nodes out, so the cursor can go there
            tree.move_cursor(nodes[selected])
        lines = [f"{i}. {op.describe()}" for i, op in enumerate(self.ops, 1)]
        self.query_one("#ops", Static).update("\n".join(lines) or "No operations.")

    def action_toggle_all(self) -> None:
        tree = self.query_one("#targets", PathTree)
        self._remember_folds(tree)
        folders = {path for path in self._folders() if path}
        self.collapsed = set() if folders <= self.collapsed else folders
        self.rebuild(keep_folds=False)

    def _folders(self) -> set[str]:
        targets = pathops.apply_ops(self.base, self.ops)
        return {t.rsplit("/", i)[0] for t in targets for i in range(1, t.count("/") + 1)}

    def action_filter(self) -> None:
        box = self.query_one("#filter", Input)
        box.display = True
        box.focus()

    @on(Input.Changed, "#filter")
    def filter_changed(self, event: Input.Changed) -> None:
        self.filter = event.value.strip().casefold()
        self.rebuild()

    @on(Input.Submitted, "#filter")
    def filter_done(self) -> None:
        self.query_one("#targets").focus()

    def _selected(self) -> str | None:
        node = self.query_one("#targets", PathTree).cursor_node
        return node.data if node is not None else None

    def _add(self, op: PathOp) -> None:
        try:
            self.ops.append(pathops.validate(op))
        except pathops.PathOpError as exc:
            self.notify(str(exc), severity="error")
            return
        self.rebuild()

    def action_remove(self) -> None:
        if path := self._selected():
            self._add(PathOp(OP_REMOVE, path))

    def action_rename(self) -> None:
        path = self._selected()
        if not path:
            return

        def done(new_path: str | None) -> None:
            if new_path and new_path != path:
                self._add(PathOp(OP_RENAME, path, new_path))

        self.app.push_screen(TextPromptScreen("Rename / move to", path), done)

    def action_undo(self) -> None:
        if self.ops:
            self.ops.pop()
            self.rebuild()

    def action_save(self) -> None:
        self.dismiss(self.ops)

    def action_cancel(self) -> None:
        """Escape leaves the filter first, and only then the dialog."""
        box = self.query_one("#filter", Input)
        if box.display:
            box.display = False
            box.value = ""
            self.query_one("#targets").focus()
            return
        self.dismiss(None)
