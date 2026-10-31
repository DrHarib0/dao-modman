"""Modal dialogs."""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from rich.markup import escape
from rich.style import Style
from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Input,
    Label,
    OptionList,
    ProgressBar,
    SelectionList,
    Static,
    Tree,
)
from textual.widgets.option_list import Option
from textual.widgets.selection_list import Selection
from textual.widgets.tree import TreeNode

from ..core import deploy, nexus, opener, pathops, report
from ..core.library import Library
from ..core.models import OP_EXCLUDE, OP_INCLUDE, Mod, PathOp
from ..core.moditems import ModItem
from ..core.nexus import NexusClient, NexusError, NxmLink
from ..core.overridepkg import OptionKey

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
            with VerticalScroll(classes="message"):  # a long message scrolls, the keys stay
                yield Static(self.message, markup=False)
            yield Label("[b]y[/] yes   [b]n[/] no", classes="hint keys")

    def action_answer(self, value: bool) -> None:
        self.dismiss(value)


class UnapplyScreen(ModalScreen[str | None]):
    """Asked when unapply would leave things behind. Result: "all", "ours" or None.

    Removing everything cannot be undone, so enter does not confirm here.
    """

    BINDINGS = [
        Binding("y", "answer('all')", "Remove everything"),
        Binding("o", "answer('ours')", "Only dao-modman's files"),
        Binding("n,escape", "answer(None)", "Cancel"),
    ]

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide"):
            with VerticalScroll(classes="message"):
                yield Static(self.message, markup=False)
            yield Label(
                "[b]y[/] remove everything (cannot be undone)   "
                "[b]o[/] only dao-modman's files   [b]n[/] cancel",
                classes="hint keys",
            )

    def action_answer(self, value: str | None) -> None:
        self.dismiss(value)


SPLASH_ART = """\
██████╗  █████╗  ██████╗
██╔══██╗██╔══██╗██╔═══██╗
██║  ██║███████║██║   ██║
██║  ██║██╔══██║██║   ██║
██████╔╝██║  ██║╚██████╔╝
╚═════╝ ╚═╝  ╚═╝ ╚═════╝"""
SPLASH_TAGLINE = """\
─────────── ◆ ───────────
     m o d   m a n a g e r"""


class ProgressScreen(ModalScreen[None]):
    """Shown while a long operation runs. It has no keys: the operation cannot be left."""

    def __init__(self, title: str, *, splash: bool = False) -> None:
        super().__init__()
        self.title_text = title
        self.splash = splash  # the program's banner above, for the startup

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            if self.splash:
                yield Static(SPLASH_ART, id="splash-art", markup=False)
                yield Static(SPLASH_TAGLINE, id="splash-tagline", markup=False)
            yield Label(self.title_text, classes="title", markup=False)
            yield ProgressBar(id="work-progress")
            yield Static("", id="work-detail", markup=False)

    def advance(self, phase: str, done: int, total: int) -> None:
        self.query_one("#work-progress", ProgressBar).update(total=total, progress=done)
        self.query_one("#work-detail", Static).update(f"{phase}: {done} / {total}")


class TextScreen(ModalScreen[None]):
    """A report to read: scrollable text with a title."""

    BINDINGS = [Binding("escape,q", "close", "Close")]

    def __init__(self, title: str, text: str) -> None:
        super().__init__()
        self.title_text = title
        self.text = text

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide tall"):
            yield Label(self.title_text, classes="title", markup=False)
            with VerticalScroll(id="report"):
                yield Static(self.text, markup=False)
            yield Label("[b]\u2191 \u2193[/] scroll   [b]esc[/] close", classes="hint")

    def on_mount(self) -> None:
        self.query_one("#report").focus()

    def action_close(self) -> None:
        self.dismiss(None)


class ItemsScreen(ModalScreen[None]):
    """The items a mod adds: in-game name and kind of item; the file names on request."""

    BINDINGS = [
        Binding("escape,q", "close", "Close"),
        Binding("f", "toggle_files", "File names"),
    ]

    def __init__(self, title: str, items: list[ModItem], footnotes: list[str]) -> None:
        super().__init__()
        self.title_text = title
        self.items = items
        self.footnotes = footnotes
        self.show_files = False

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide tall"):
            yield Label(self.title_text, classes="title", markup=False)
            with VerticalScroll(id="report"):
                yield Static(self.render_items(), id="items", markup=False)
            yield Label(
                "[b]f[/] file names   [b]\u2191 \u2193[/] scroll   [b]esc[/] close", classes="hint"
            )

    def render_items(self) -> str:
        names = [item.name if item.named else f"{item.file} (no name)" for item in self.items]
        width = max((len(name) for name in names), default=0)
        kinds = max((len(item.type) for item in self.items), default=0)
        lines = []
        for name, item in zip(names, self.items, strict=True):
            columns = [f"{name:<{width}}"]
            if kinds:  # no column if no item's kind is known
                columns.append(f"{item.type:<{kinds}}")
            if self.show_files and item.named:
                columns.append(item.file)
            lines.append("  ".join(columns).rstrip())
        if self.footnotes:
            lines += ["", *self.footnotes] if lines else self.footnotes
        return "\n".join(lines)

    def on_mount(self) -> None:
        self.query_one("#report").focus()

    def action_toggle_files(self) -> None:
        self.show_files = not self.show_files
        self.query_one("#items", Static).update(self.render_items())

    def action_close(self) -> None:
        self.dismiss(None)


class KeysScreen(ModalScreen[None]):
    """Every key of the main screen, by section."""

    BINDINGS = [Binding("escape,question_mark,q", "close", "Close")]

    def __init__(self, sections: list[tuple[str, list[tuple[str, str]]]]) -> None:
        super().__init__()
        self.sections = sections

    def compose(self) -> ComposeResult:
        width = max(len(key) for _section, keys in self.sections for key, _help in keys)
        with VerticalScroll(classes="dialog"):
            yield Label("Keys", classes="title")
            for section, keys in self.sections:
                lines = "\n".join(f"  {key:<{width}}   {text}" for key, text in keys)
                yield Label(section, classes="section", markup=False)
                yield Static(lines, markup=False)
            yield Label("[b]esc[/] close", classes="hint")

    def action_close(self) -> None:
        self.dismiss(None)


class TextPromptScreen(ModalScreen[str | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self, title: str, value: str = "", placeholder: str = "", *, password: bool = False
    ) -> None:
        super().__init__()
        self.title_text = title
        self.value = value
        self.placeholder = placeholder
        self.password = password  # typed text is not shown, e.g. an API key

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_text, classes="title", markup=False)
            yield Input(value=self.value, placeholder=self.placeholder, password=self.password)
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


class ChoiceScreen(ModalScreen[str | None]):
    """Pick one entry from a short list. Result: the id of the chosen entry."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self, title: str, choices: list[tuple[str, str]], *, selected: str | None = None
    ) -> None:
        super().__init__()
        self.title_text = title
        self.choices = choices
        self.selected = selected

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self.title_text, classes="title", markup=False)
            yield OptionList(*(Option(escape(label), id=key) for key, label in self.choices))
            yield Label("[b]enter[/] choose   [b]esc[/] cancel", classes="hint")

    def on_mount(self) -> None:
        ids = [key for key, _label in self.choices]
        if self.selected in ids:
            self.query_one(OptionList).highlighted = ids.index(self.selected)

    @on(OptionList.OptionSelected)
    def select(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class OptionsScreen(ModalScreen[None]):
    """Choose the variants of an override package's options."""

    BINDINGS = [Binding("escape,q,p", "close", "Close")]

    def __init__(self, library: Library, mod: Mod) -> None:
        super().__init__()
        self.library = library
        self.mod = mod
        self.keys: list[OptionKey] = []
        self.files: set[str] = set()  # names of all files in the mod

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide"):
            yield Label(f"Options of {self.mod.name}", classes="title", markup=False)
            yield OptionList(id="option-keys")
            yield Label("[b]enter[/] change   [b]esc[/] close", classes="hint")

    def on_mount(self) -> None:
        self.rebuild()

    def describe(self, key: OptionKey) -> str:
        """`Section / Key: value  ← variant file`, with a mark if it is not the default."""
        chosen = key.chosen(self.mod.options)
        if chosen is None:
            return f"{key.section} / {key.name}: (no usable default)"
        changed = "" if key.is_default(self.mod.options) else "  (changed)"
        # a default that the package ships only as the original file has no variant file
        source = chosen.file if chosen.file.casefold() in self.files else key.original
        return f"{key.section} / {key.name}: {chosen.label}{changed}  \u2190 {source}"

    def rebuild(self, highlight: int = 0) -> None:
        self.keys = [key for package in self.library.options(self.mod) for key in package.keys]
        listing = self.library.list_files(self.mod.id)
        self.files = {rel.rpartition("/")[2].casefold() for rel in listing}
        options = self.query_one("#option-keys", OptionList)
        options.clear_options()
        options.add_options([Option(escape(self.describe(key))) for key in self.keys])
        if self.keys:
            options.highlighted = min(highlight, len(self.keys) - 1)

    @on(OptionList.OptionSelected, "#option-keys")
    def change(self, event: OptionList.OptionSelected) -> None:
        index = event.option_index
        key = self.keys[index]
        default, chosen = key.value(key.default), key.chosen(self.mod.options)
        choices = [
            (value.label, value.label + (" (default)" if value is default else ""))
            for value in key.values
        ]

        def done(label: str | None) -> None:
            if label is not None:
                self.library.set_option(self.mod, key, label)
                self.rebuild(index)

        self.app.push_screen(
            ChoiceScreen(
                f"{key.section} / {key.name}", choices, selected=chosen.label if chosen else None
            ),
            done,
        )

    def action_close(self) -> None:
        self.dismiss(None)


class NexusAddScreen(ModalScreen[tuple[int, list[int], NxmLink | None] | None]):
    """Pick files of a Nexus mod. Result: `(mod_id, file_ids, nxm link or None)`."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "confirm", "Look up / download", priority=True, show=False),
    ]

    def __init__(
        self, client: NexusClient, premium: bool, installed: set[tuple[int, int]] | None = None
    ) -> None:
        super().__init__()
        self.client = client
        self.premium = premium
        self.installed = installed or set()  # (mod id, file id) already in the library
        self.mod_id: int | None = None

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide"):
            yield Label("Add mod from Nexus", classes="title")
            yield Input(placeholder="mod URL, mod id or nxm:// link")
            yield Static("", id="info", markup=False)
            yield SelectionList[int](id="files")
            yield Label(
                "[b]enter[/] look up / download   [b]space[/] select file   [b]esc[/] cancel",
                classes="hint",
            )

    def on_mount(self) -> None:
        self.query_one("#files").display = False
        if not self.premium:
            self.query_one("#info", Static).update(
                "Free Nexus account: look the mod up and select files to open their download"
                " pages in the browser, or paste an nxm:// link."
            )

    def action_confirm(self) -> None:
        box = self.query_one(Input)
        if self.focused is box:
            self.lookup(box.value.strip())
            return
        files = self.query_one("#files", SelectionList)
        chosen = list(files.selected)
        if not chosen and files.highlighted is not None:  # none checked: the highlighted one
            chosen = [files.get_option_at_index(files.highlighted).value]
        if self.mod_id is not None and chosen:
            self.dismiss((self.mod_id, chosen, None))

    @work(exclusive=True)
    async def lookup(self, text: str) -> None:
        info = self.query_one("#info", Static)
        try:
            if text.startswith("nxm:"):
                link = nexus.parse_nxm(text)
                if link.game != nexus.GAME:
                    raise NexusError(f"that link is for {link.game!r}, not {nexus.GAME!r}")
                self.dismiss((link.mod_id, [link.file_id], link))
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
        files.sort(key=lambda f: f.get("uploaded_timestamp", 0), reverse=True)
        self.mod_id = mod_id
        hint = "" if self.premium else " - selected files open in the browser (free account)"
        info.update(f"{mod_info.get('name', mod_id)}: {len(files)} file(s){hint}")
        installed = {file_id for owner, file_id in self.installed if owner == mod_id}
        preselected = set(nexus.default_files(files)) - installed
        options = self.query_one("#files", SelectionList)
        options.clear_options()
        for file in files:
            file_id = int(file["file_id"])
            size_mb = (file.get("size_kb") or 0) / 1024
            label = (
                f"{file.get('name', '?')}  v{file.get('version') or '?'}  "
                f"[{file.get('category_name', '?')}]  {size_mb:.1f} MB"
            )
            if file_id in installed:
                label += "  (installed)"
            options.add_option(Selection(escape(label), file_id, file_id in preselected))
        options.display = True
        if files:
            options.highlighted = 0
            options.focus()

    def action_cancel(self) -> None:
        self.dismiss(None)


class PathTree(Tree[str]):
    """A tree of paths that folds with the arrow keys, like a file manager.

    Nodes whose data is a path get the look of Textual's DirectoryTree: folder and file
    icons, folders bold, the extension of a file name set apart. Other nodes (the conflict
    view's group lines) keep their plain label.
    """

    ICON_NODE = "\U0001f4c1 "
    ICON_NODE_EXPANDED = "\U0001f4c2 "
    ICON_FILE = "\U0001f4c4 "
    IGNORED_MARK = "  \u00b7 ignored"

    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "path-tree--folder",
        "path-tree--file",
        "path-tree--extension",
        "path-tree--ignored",
    }

    DEFAULT_CSS = """
    PathTree {
        & > .path-tree--folder {
            text-style: bold;
        }
        & > .path-tree--extension {
            color: $accent;
        }
        & > .path-tree--ignored {
            text-style: dim italic;
        }
    }
    """

    BINDINGS = [
        Binding("left", "fold", "Collapse", show=False),
        Binding("right", "unfold", "Expand", show=False),
    ]

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.ignored: set[str] = set()  # paths of files shown, but never deployed

    def render_label(self, node: TreeNode[str], base_style: Style, style: Style) -> Text:
        label = node._label.copy()
        label.stylize(style)
        if not self.is_mounted or not isinstance(node.data, str):
            return label
        if node.allow_expand:
            icon = self.ICON_NODE_EXPANDED if node.is_expanded else self.ICON_NODE
            label.stylize_before(self.get_component_rich_style("path-tree--folder", partial=True))
        else:
            icon = self.ICON_FILE
            # the name only: not the source note or the ignored mark after it
            name = label.plain.split("  ", 1)[0]
            dot = name.rfind(".")
            if dot > 0:
                extension = self.get_component_rich_style("path-tree--extension", partial=True)
                label.stylize(extension, dot, len(name))
        if node.data in self.ignored:  # a file left out, or a folder with only such files
            label.stylize(self.get_component_rich_style("path-tree--ignored", partial=True))
        return Text.assemble((icon, base_style), label)

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

    BINDINGS = [Binding("escape,q,v", "close", "Close")]

    def __init__(
        self,
        library: Library,
        mod: Mod | None = None,
        foreign: list[tuple[str, Path]] | None = None,
    ) -> None:
        super().__init__()
        self.library = library
        self.foreign = foreign  # files in the mod directories that are not managed
        self.mod_id = mod.id if mod else None  # only what involves this mod; None: everything
        self.title_text = f"Conflicts of {mod.name}" if mod else "Conflicts"

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide tall"):
            yield Label(f"{self.title_text} (the last mod wins)", classes="title", markup=False)
            yield PathTree("Conflicts", id="conflicts")
            yield Label("[b]\u2190 \u2192[/] fold   [b]esc[/] close", classes="hint")

    def on_mount(self) -> None:
        tree = self.query_one("#conflicts", PathTree)
        tree.show_root = False
        plan = deploy.build_plan(self.library, self.foreign)
        for group in report.conflict_groups(self.library, plan, self.mod_id):
            group_node = tree.root.add(escape(report.group_line(group)), expand=True)
            for conflict in group.conflicts:
                file_node = group_node.add(escape(conflict.name), expand=True)
                for copy in report.provider_lines(self.library, conflict):
                    file_node.add_leaf(escape(copy))
        if merged := report.merged_lines(self.library, plan, self.mod_id):
            merged_node = tree.root.add("Merged (no mod loses)", expand=True)
            for line in merged:
                merged_node.add_leaf(escape(line))
        tree.focus()

    def action_close(self) -> None:
        self.dismiss(None)


class PathOpsTree(PathTree):
    """The path editor's tree: space includes or excludes the selected entry."""

    BINDINGS = [Binding("space", "screen.toggle", "Include / exclude", show=False)]


class PathOpsScreen(ModalScreen[list[PathOp] | None]):
    """Choose which of a mod's files are installed: exclude or include files and folders.

    The tree shows every file of the mod where it would go; what is left out, by an ignore
    pattern or by the user, is dimmed and marked.
    """

    BINDINGS = [
        Binding("u", "undo", "Undo last"),
        Binding("c", "toggle_all", "Collapse/expand all"),
        Binding("slash", "filter", "Filter"),
        Binding("o", "open", "Open the file"),
        Binding("s", "save", "Save"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        mod: Mod,
        base: dict[str, str],
        *,
        files_dir: Path | None = None,
        ignored: set[str] | None = None,
    ) -> None:
        super().__init__()
        self.mod = mod
        self.base = base  # target -> source, ignored files included
        self.files_dir = files_dir  # where the sources are, for opening them
        self.ignored = ignored or set()  # sources the ignore patterns leave out
        self.ops = list(mod.ops)
        self.history: list[list[PathOp]] = []  # the operations before each change, for undo
        self.left_out: set[str] = set()  # targets not installed
        self.filter = ""
        self.collapsed: set[str] = set()  # folders the user folded, kept across rebuilds

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide tall"):
            yield Label(f"Files of {self.mod.name}", classes="title", markup=False)
            yield PathOpsTree("Dragon Age/", id="targets")
            yield Input(placeholder="filter by part of a file or folder name", id="filter")
            yield Label(
                "[b]space[/] include/exclude   [b]u[/] undo   [b]o[/] open   [b]/[/] filter   "
                "[b]\u2190 \u2192[/] fold   [b]c[/] fold all   [b]s[/] save   [b]esc[/] cancel",
                classes="hint",
            )

    def on_mount(self) -> None:
        self.query_one("#filter").display = False
        self.rebuild()

    def _left_out(self, ops: list[PathOp]) -> set[str]:
        return set(pathops.select(self.base, self.ignored.__contains__, ops)[1])

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

    def _below(self, path: str) -> list[str]:
        """The files at or below a path."""
        folded = path.casefold()
        return [
            t for t in self.base if t.casefold() == folded or t.casefold().startswith(folded + "/")
        ]

    def rebuild(self, *, keep_folds: bool = True) -> None:
        tree = self.query_one("#targets", PathTree)
        selected = self._selected()
        if keep_folds:
            self._remember_folds(tree)
        tree.clear()
        tree.root.expand()
        nodes = {"": tree.root}
        self.left_out = self._left_out(self.ops)
        # a folder counts as left out when no file below it is installed
        with_installed = {
            target.rsplit("/", depth)[0]
            for target in self.base
            if target not in self.left_out
            for depth in range(1, target.count("/") + 1)
        }
        tree.ignored = self.left_out | (self._folders() - with_installed - {""})
        for target, source in sorted(self.base.items()):
            if self.filter and not self._matches(target):
                continue
            parts = target.split("/")
            for depth in range(1, len(parts)):
                path = "/".join(parts[:depth])
                if path not in nodes:
                    parent = nodes["/".join(parts[: depth - 1])]
                    expand = bool(self.filter) or path not in self.collapsed
                    label = escape(parts[depth - 1])
                    if path in tree.ignored:
                        label += PathTree.IGNORED_MARK
                    nodes[path] = parent.add(label, data=path, expand=expand)
            # a file installed under another name than it has in the mod shows where it is
            # from: the variant chosen for an option of an override package
            came_from = source.rpartition("/")[2]
            label = parts[-1]
            if came_from.casefold() != label.casefold():
                label += f"  \u2190 {came_from}"
            if target in tree.ignored:
                label += PathTree.IGNORED_MARK
            nodes[target] = nodes["/".join(parts[:-1])].add_leaf(escape(label), data=target)
        # put the cursor back on the same entry, or the closest folder that still exists
        while selected and selected not in nodes:
            selected = selected.rpartition("/")[0]
        if selected:
            tree._tree_lines  # noqa: B018 - lays the new nodes out, so the cursor can go there
            tree.move_cursor(nodes[selected])

    def action_toggle(self) -> None:
        """Exclude what is installed below the selection, or include it if all is left out.

        Earlier operations on exactly this path are dropped first; a new one is only added
        if it still changes something, so the operations stay few.
        """
        path = self._selected()
        if not path:
            return
        files = self._below(path)
        include = all(target in self.left_out for target in files)
        ops = [op for op in self.ops if op.path.casefold() != path.casefold()]
        left_out = self._left_out(ops)
        done = (
            not any(t in left_out for t in files) if include else all(t in left_out for t in files)
        )
        if not done:
            ops.append(PathOp(OP_INCLUDE if include else OP_EXCLUDE, path))
        self.history.append(self.ops)
        self.ops = ops
        self.rebuild()

    def action_toggle_all(self) -> None:
        tree = self.query_one("#targets", PathTree)
        self._remember_folds(tree)
        folders = {path for path in self._folders() if path}
        self.collapsed = set() if folders <= self.collapsed else folders
        self.rebuild(keep_folds=False)

    def _folders(self) -> set[str]:
        return {t.rsplit("/", i)[0] for t in self.base for i in range(1, t.count("/") + 1)}

    def action_filter(self) -> None:
        box = self.query_one("#filter", Input)
        box.display = True
        box.focus()

    @on(Input.Changed, "#filter")
    def filter_changed(self, event: Input.Changed) -> None:
        event.stop()  # the mod list's filter box has the same id: its handler must not see this
        self.filter = event.value.strip().casefold()
        self.rebuild()

    @on(Input.Submitted, "#filter")
    def filter_done(self, event: Input.Submitted) -> None:
        event.stop()
        self.query_one("#targets").focus()

    def _selected(self) -> str | None:
        node = self.query_one("#targets", PathTree).cursor_node
        return node.data if node is not None else None

    def action_open(self) -> None:
        """Open the selected file's copy in the library with the desktop's program for it."""
        source = self.base.get(self._selected() or "")
        if source is None or self.files_dir is None:
            self.notify("Select a file to open")
            return
        path = self.files_dir / source

        def go(confirmed: bool | None = True) -> None:
            if not confirmed:
                return
            try:
                opener.open_file(path)
            except opener.OpenerError as exc:
                self.notify(str(exc), severity="error", markup=False)

        if opener.is_executable(path):
            message = f"{path.name} is a program: opening it may run it.\n\nOpen it anyway?"
            self.app.push_screen(ConfirmScreen(message), go)
        else:
            go()

    def action_undo(self) -> None:
        if self.history:
            self.ops = self.history.pop()
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
