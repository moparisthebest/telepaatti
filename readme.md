xmpp-ircd
----------

This is a [fork](https://github.com/moparisthebest/xmpp-ircd) of a [fork](https://github.com/julien-picalausa/telepaatti)
of a [fork](https://github.com/davux/telepaatti) of [telepaatti](http://23.fi/telepaatti/), which was originally written
as a way for one user to connect to a XMPP server from their IRC client.

This particular fork aims to be ran as an IRC server connected to a single XMPP MUC as a standard XMPP component.  This
will hopefully allow IRC server operators to migrate to hosting a real XMPP MUC with minimal headache or complaining from
hardcore IRC users, and also allow XMPP MUC operators to easily add IRC support.

Usage
-----

    ./xmpp-ircd.py --muc-server=chat.example.com --component-name=irc.example.com --component-pass=irc

Will connect to 127.0.0.1:5347 as an XMPP component and serve the MUC chat.example.com as an IRC server on port 6667.

prosody for example would need this component configuration for the above command:

    Component "chat.example.com" "muc"

    Component "irc.example.com"
        component_secret = "irc"

Then, whether an XMPP user connects to [xmpp:example@chat.example.com?join](xmpp:example@chat.example.com?join) or an
IRC user to [irc://irc.example.com:6667/example](irc://irc.example.com:6667/example) they will both be in the same channel,
hopefully unable to tell the other is using a completely different protocol.

Development
-----------

Useful documentation:
  * [XEP-0045: Multi-User Chat](https://xmpp.org/extensions/xep-0045.html)
  * [RFC-1459: Internet Relay Chat Protocol](https://tools.ietf.org/html/rfc1459)
  * Unfortunately real traffic between an IRC client and server may be the best documentation, as it tends to ignore the
    RFC when deemed convenient.

Useful command to watch real IRC traffic between actual client and server, connect client to localhost:4444:

    socat -v TCP-LISTEN:4444,fork OPENSSL:irc.freenode.net:6697

todo:
  * NickServ/SASL auth
  * finish /list /whois
  * handle XMPP disconnecting
  * handle shutdown cleanly
  * init scripts?
  * other IRC commands?

known issues:
  * nick changes are hacky, can lock up gajim private messages rarely somehow...
  * Nicks are only unique per-channel in XMPP, but per-server in IRC, I don't know that there IS a good solution for this.
    The main problem this brings up is with private messaging, there is no way to know who you are chatting with.  Nick
    changes are also affected, but that only prevents you from changing your nick in a channel if you are joined with any
    other channels where the nick is taken.

License - GNU/GPLv3
-------------------
    xmpp-ircd, IRC to Jabber/XMPP gateway.
    forked from Telepaatti

    Copyright (C) 2007-2009 Petteri Klemola
    Copyright (C) 2015 moparisthebest

    xmpp-ircd is free software; you can redistribute it and/or modify it
    under the terms of the GNU General Public License version 3 as
    published by the Free Software Foundation.

    xmpp-ircd is distributed in the hope that it will be useful, but
    WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
    General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program; if not, write to the Free Software
    Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA
    02110-1301, USA.
