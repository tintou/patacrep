"""Sorted list of songs.

This plugin provides keyword 'sorted', used to include a sorted list of songs
to a songbook.
"""

import logging
import unidecode

from patacrep import files
from patacrep.content import ContentError, EmptyContentList
from patacrep.content import process_content
from patacrep.content.song import OnlySongsError

LOGGER = logging.getLogger(__name__)

DEFAULT_SORT = ['by', 'album', '@title']

def normalize_string(string):
    """Return a normalized string.

    Normalized means:
    - no surrounding spaces;
    - lower case;
    - passed through unidecode.unidecode().
    """
    return unidecode.unidecode(string.lower().strip())

def normalize_field(field):
    """Return a normalized field, it being a string or a list of strings."""
    if isinstance(field, str):
        return normalize_string(field)
    elif isinstance(field, list) or isinstance(field, tuple):
        return [normalize_field(string) for string in field]

def key_generator(sort):
    """Return a function that returns the list of values used to sort the song.

    Arguments:
        - sort: the list of keys used to sort.
    """

    def ordered_song_keys(songrenderer):
        """Return the list of values used to sort the song."""
        song = songrenderer.song
        songkey = []
        for key in sort:
            if key == "@title":
                field = song.unprefixed_titles
            elif key == "@path":
                field = song.fullpath
            elif key == "by":
                field = song.authors
            else:
                try:
                    field = song.data[key]
                except KeyError:
                    LOGGER.debug(
                        "Ignoring unknown key '{}' for song {}.".format(
                            key,
                            files.relpath(song.fullpath),
                            )
                        )
                    field = ""
            songkey.append(normalize_field(field))
        return songkey
    return ordered_song_keys

#pylint: disable=unused-argument
def parse(keyword, config, argument, contentlist):
    """Return a sorted list of songs contained in 'contentlist'.

    Arguments:
        - keyword: the string 'sorted';
        - config: the current songbook configuration dictionary;
        - argument: the list of the fields used to sort songs, as strings
          separated by commas (e.g. "by, album, @title");
        - contentlist: the list of content to be sorted. If this content
          contain something else than a song, an exception is raised.
    """
    if argument:
        sort = [key.strip() for key in argument.split(",")]
    else:
        sort = DEFAULT_SORT
    try:
        songlist = process_content(contentlist, config)
    except OnlySongsError as error:
        return EmptyContentList(errors=[ContentError(keyword, (
            "Content list of this keyword can be only songs (or content "
            "that result into songs), and the following are not:" +
            str(error.not_songs)
            ))])
    return sorted(songlist, key=key_generator(sort))

CONTENT_PLUGINS = {'sorted': parse}
