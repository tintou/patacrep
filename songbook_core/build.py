#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""Build a songbook, according to parameters found in a .sb file."""

import codecs
import glob
import logging
import os.path
import re
from subprocess import Popen, PIPE, call

from songbook_core import __DATADIR__
from songbook_core import errors
from songbook_core.files import recursive_find
from songbook_core.index import process_sxd
from songbook_core.songs import Song, SongbookContent
from songbook_core.templates import TexRenderer

LOGGER = logging.getLogger(__name__)
EOL = "\n"
DEFAULT_AUTHWORDS = {
        "after": ["by"],
        "ignore": ["unknown"],
        "sep": ["and"],
        }
DEFAULT_STEPS = ['tex', 'pdf', 'sbx', 'pdf', 'clean']
GENERATED_EXTENSIONS = [
        "_auth.sbx",
        "_auth.sxd",
        ".aux",
        ".log",
        ".out",
        ".sxc",
        ".tex",
        "_title.sbx",
        "_title.sxd",
        ]



# pylint: disable=too-few-public-methods
class Songbook(object):
    """Represent a songbook (.sb) file.

    - Low level: provide a Python representation of the values stored in the
      '.sb' file.
    - High level: provide some utility functions to manipulate these data.
    """

    def __init__(self, raw_songbook, basename):
        super(Songbook, self).__init__()
        self.basename = basename
        # Default values: will be updated while parsing raw_songbook
        self.config = {
                'template': "default.tex",
                'lang': 'english',
                'sort': [u"by", u"album", u"@title"],
                'content': None,
                }
        self._parse_raw(raw_songbook)

    @staticmethod
    def _set_songs_default(config):
        """Set the default values for the Song() class.

        Argument:
            - config : a dictionary containing the configuration
        """
        Song.sort = config['sort']
        if 'titleprefixwords' in config:
            Song.prefixes = config['titleprefixwords']
        else:
            Song.prefixes = []
        Song.authwords['after'] = [
                re.compile(r"^.*%s\b(.*)" % after)
                for after
                in config['authwords']["after"]
                ]
        Song.authwords['ignore'] = config['authwords']['ignore']
        Song.authwords['sep'] = [
                re.compile(r"^(.*)%s (.*)$" % sep)
                for sep in ([
                    " %s" % sep for sep in config['authwords']["sep"]
                            ] + [','])
                ]

    def _parse_raw(self, raw_songbook):
        """Parse raw_songbook.

        The principle is: some special keys have their value processed; others
        are stored verbatim in self.config.
        """
        self.config.update(raw_songbook)
        self._set_datadir()

        # Compute song list
        if self.config['content'] is None:
            self.config['content'] = [(
                    "song",
                    os.path.relpath(
                        filename,
                        os.path.join(self.config['datadir'][0], 'songs'),
                        ))
                    for filename
                    in recursive_find(
                                os.path.join(self.config['datadir'][0], 'songs'),
                                '*.sg',
                                )
                    ]
        else:
            content = self.config["content"]
            self.config["content"] = []
            for elem in content:
                if isinstance(elem, basestring):
                    self.config["content"].append(("song", elem))
                elif isinstance(elem, list):
                    self.config["content"].append((elem[0], elem[1]))
                else:
                    raise errors.SBFileError(
                                 "Syntax error: could not decode the content "
                                 "of {0}".format(self.basename)
                                 )

        # Ensure self.config['authwords'] contains all entries
        for (key, value) in DEFAULT_AUTHWORDS.items():
            if key not in self.config['authwords']:
                self.config['authwords'][key] = value

    def _set_datadir(self):
        """Set the default values for datadir"""
        try:
            if isinstance(self.config['datadir'], basestring):
                self.config['datadir'] = [self.config['datadir']]
        except KeyError:  # No datadir in the raw_songbook
            self.config['datadir'] = [os.path.abspath('.')]

        abs_datadir = []
        for path in self.config['datadir']:
            if os.path.exists(path) and os.path.isdir(path):
                abs_datadir.append(os.path.abspath(path))
            else:
                LOGGER.warning("Ignoring non-existent datadir '{}'.".format(path))

        abs_datadir.append(__DATADIR__)

        self.config['datadir'] = abs_datadir

    def _parse_songs(self):
        """Parse content included in songbook."""
        self.contentlist = SongbookContent(self.config['datadir'])
        self.contentlist.append_list(self.config['content'])

    def write_tex(self, output):
        """Build the '.tex' file corresponding to self.

        Arguments:
        - output: a file object, in which the file will be written.
        """
        self._parse_songs()
        renderer = TexRenderer(
                self.config['template'],
                self.config['datadir'],
                self.config['lang'],
                )

        context = renderer.get_variables()
        context.update(self.config)
        context['titleprefixkeys'] = ["after", "sep", "ignore"]
        context['content'] = self.contentlist
        context['filename'] = output.name[:-4]

        self._set_songs_default(context)
        renderer.render_tex(output, context)


class SongbookBuilder(object):
    """Provide methods to compile a songbook."""

    # if False, do not expect anything from stdin.
    interactive = False
    # if True, allow unsafe option, like adding the --shell-escape to pdflatex
    unsafe = False
    # Options to add to pdflatex
    _pdflatex_options = []
    # Dictionary of functions that have been called by self._run_once(). Keys
    # are function; values are return values of functions.
    _called_functions = {}

    def __init__(self, raw_songbook, basename):
        # Representation of the .sb songbook configuration file.
        self.songbook = Songbook(raw_songbook, basename)
        # Basename of the songbook to be built.
        self.basename = basename

    def _run_once(self, function, *args, **kwargs):
        """Run function if it has not been run yet.

        If it as, return the previous return value.
        """
        if function not in self._called_functions:
            self._called_functions[function] = function(*args, **kwargs)
        return self._called_functions[function]

    def _set_latex(self):
        """Set LaTeX options."""
        if self.unsafe:
            self._pdflatex_options.append("--shell-escape")
        if not self.interactive:
            self._pdflatex_options.append("-halt-on-error")

    def build_steps(self, steps=None):
        """Perform steps on the songbook by calling relevant self.build_*()

        Arguments:
        - steps: list of steps to perform to compile songbook. Available steps
          are:
          - tex: build .tex file from templates;
          - pdf: compile .tex using pdflatex;
          - sbx: compile song and author indexes;
          - clean: remove temporary files,
          - any string beginning with a sharp sign (#): it is interpreted as a
            command to run in a shell.
        """
        if not steps:
            steps = DEFAULT_STEPS

        for step in steps:
            if step == 'tex':
                self.build_tex()
            elif step == 'pdf':
                self.build_pdf()
            elif step == 'sbx':
                self.build_sbx()
            elif step == 'clean':
                self.clean()
            elif step.startswith("#"):
                self.build_custom(step[1:])
            else:
                # Unknown step name
                raise errors.UnknownStep(step)

    def build_tex(self):
        """Build .tex file from templates"""
        LOGGER.info("Building '{}.tex'…".format(self.basename))
        with codecs.open(
                "{}.tex".format(self.basename), 'w', 'utf-8',
                ) as output:
            self.songbook.write_tex(output)

    def build_pdf(self):
        """Build .pdf file from .tex file"""
        LOGGER.info("Building '{}.pdf'…".format(self.basename))
        self._run_once(self._set_latex)
        process = Popen(
                ["pdflatex"] + self._pdflatex_options + [self.basename],
                stdin=PIPE,
                stdout=PIPE,
                stderr=PIPE,
                env=os.environ)
        if not self.interactive:
            process.stdin.close()
        log = ''
        line = process.stdout.readline()
        while line:
            log += line
            line = process.stdout.readline()
        LOGGER.debug(log)

        process.wait()

        if process.returncode:
            raise errors.LatexCompilationError(self.basename)

    def build_sbx(self):
        """Make index"""
        LOGGER.info("Building indexes…")
        sxd_files = glob.glob("%s_*.sxd" % self.basename)
        for sxd_file in sxd_files:
            LOGGER.debug("Processing " + sxd_file)
            idx = process_sxd(sxd_file)
            with codecs.open(sxd_file[:-3] + "sbx", "w", "utf-8") as index_file:
                index_file.write(idx.entries_to_str())

    @staticmethod
    def build_custom(command):
        """Run a shell command"""
        LOGGER.info("Running '{}'…".format(command))
        exit_code = call(command, shell=True)
        if exit_code:
            raise errors.StepCommandError(command, exit_code)

    def clean(self):
        """Clean (some) temporary files used during compilation.

        Depending of the LaTeX modules used in the template, there may be others
        that are not deleted by this function."""
        LOGGER.info("Cleaning…")
        for ext in GENERATED_EXTENSIONS:
            if os.path.isfile(self.basename + ext):
                try:
                    os.unlink(self.basename + ext)
                except Exception as exception:
                    raise errors.CleaningError(self.basename + ext, exception)