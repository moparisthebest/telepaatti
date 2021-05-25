#!/usr/bin/python
"""

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
"""

import socket
import ssl
import time, datetime
import exceptions
from threading import *
from xmpp import *
import getopt, sys
import daemon
import logging
import logging.handlers
import urllib
import string
import random

STATUSSTATES = ['AVAILABLE','CHAT', 'AWAY', 'XA', 'DND', 'INVISIBLE']
XMPPIRCDVERSION = 3.0

class JabberThread(Thread):
    """Class for Jabber connection thread"""

    def __init__(self, client):
        """Constructor for JabberThread Class

        @type client: Client
        @param client: instace of xmpp.Client class
        """
        Thread.__init__(self)
        self.client = client
        self.connected = True

    def run(self):
        """When xmpp client is connected runs the client process """
        #return
        time.sleep(5)
        while self.client.Process(1) and self.connected:
            pass
        self.client.disconnect()
        self.connected = False

class ClientThread(Thread):
    """ ClientThread class for handling IRC and Jabber connections."""
    def __init__(self,socket, port, server, muc_server, component):
        """Constructor for ClientThread class

        @type socket: socket
        @type port: integer
        @type server:  server part of JID
        @param socket: socket on which the connection is made
        @param port: port of the connection
        """
        Thread.__init__(self)

        self.fullRoomJid = False

        self.component = component

        self.socket = socket
        self.port = port
        self.server = server
        self.muc_server = muc_server

        self.passwd= None

        self.nickname = None
            
        self.newnick = ''

        self.mucs = {}

        self.connected = True

        self.UIDtoJID = {}

        self.nickChangeInMucs = {}

        self.joinQueue = {}
        self.roomPingQueue = {}
        self.disconnectedMucs = {}
        self.changingNick = {}
        self.pingCounter = 0

    def printError(self, msg):
        """Error message printing for std out

        @type msg: string
        @param msg: error message
        """
        self.component.logger.error(msg)

    def printDebug(self, msg):
        """print Debug message to std out

        @type msg: string
        @param msg: debug message
        """
        self.component.logger.debug(msg)

    def getMucs(self):
        """Return joined MUC without roster MUC

        @rtype: list
        @return: list of mucs joined (without roster)
        """
        return self.mucs

    def fixNick(self, nick):
        """Fixes strange character nicknames that don't work nicely with
        IRC. This function may cause conflicts and thus unfinished.

        @type nick: string
        @param nick: nickname to fix
        @rtype: string
        @return: fixed nick
        """
        
        fixednick = unicode(nick)
        fixednick = fixednick.replace(' ', '_')
        fixednick = fixednick.replace('!', '_')
        fixednick = fixednick.replace(':', '_')
        fixednick = fixednick.replace('@', '_')
        return fixednick

    def fixChannel(self, channel):
        # fix roomname
        if self.fullRoomJid:
            return channel
        channel = unicode(channel)
        return channel[0:channel.find('@')]

    def fixChannelCommand(self, arguments):
        # do the opposite of fixChannel() above, and strip #
        if self.fullRoomJid:
            return arguments[1:]
        if ' ' in arguments:
            return arguments[1:].replace(' ', "@%s " % (self.muc_server), 1)
        else:
            return "%s@%s" % (arguments[1:], self.muc_server)

    def makeHostFromJID(self, jid):
        """ builds the host part from a given jid

        @type jid: JID
        @param jid: The JID from which to make the host part
        @rtype string
        @return valid Host part
        """

        return "%s@%s/%s" % (urllib.quote(jid.getNode()), urllib.quote(jid.getDomain()), urllib.quote(jid.getResource()))

        
    def makeNickFromJID(self, jid, is_muc_jid):
        """ builds a nick from a given jid

        @type jid: JID
        @type is_muc_jid: boolean
        @param jid: The JID from which to make a nick
        @param is_muc_jid: Whethr this JID belongs to a MUC
        @rtype:string
        @return: valid IRC nick
        """

        if is_muc_jid and not jid.getResource():
            return self.fixNick(jid.getNode())

        jid_string = u''
        if not is_muc_jid:
            jid_string = jid.getStripped()
        else:
            jid_string = unicode(jid)

        if not is_muc_jid:
            nick = self.fixNick(jid.getNode())
        else:
            nick = self.fixNick(jid.getResource())

        self.UIDtoJID[nick] = JID(jid_string)

        return nick

    def getJIDFromNick(self, nick):
        """Reverses obtains a JID corresponding to a nick generated by makeNickFromJID

        @type nick: string
        @param nick: nickname from which to recover a JID
        @rtype: JID
        @return: JID corresponding to nick
        """

        if self.UIDtoJID.has_key(nick):
            return self.UIDtoJID[nick]
        if nick.find('@') != -1:
            return JID(nick)

        return None

    def makeIRCACTION(self, msg):
        """Makes IRC action message

        @type msg: string
        @param msg: message from which to make IRC action
        @rtype: string
        @return: IRC action message
        """
        msg = '\001ACTION %s\001' % msg
        return msg

    def sendToIRC(self, msg):
        """Sends message IRC client

        @type msg: string
        @param msg: message to send
        """
        msg = msg.encode('utf-8')
        msg = "%s\r\n" % msg
        self.printDebug(msg)
        try:
            self.socket.send(msg)
        except:
            self.connected = False
            self.printError('Fatal error while trying to write irc message to socket, disconnecting [%s - %s]' % (sys.exc_info()[0], sys.exc_info()[1]))

    def sendToXMPP(self, msg):
        """Sends message XMPP server

        @type msg: string
        @param msg: message to send
        """
        msg.setFrom(self.JID)
        self.component.send(msg)

    def ircGetStatus(self, jid, room_jid):
        """Get IRC status

        @type jid: JID
        @type room_jid: JID
        @param jid: JID of the user whose status should be obtained
        @param room_jid: the room for which to get the status
        @rtype: string
        @return: IRC-style status string
        """
        sta = 'H'
        show = ''
        role = ''
        if self.mucs.has_key(room_jid):
            show = self.mucs[room_jid][jid]['show']
            role = self.mucs[room_jid][jid]['role']
        if show in ['away','xa', 'dnd']:
            sta = 'G'
        if role == 'moderator':
            sta = '%s@' % sta
        elif role == 'participant':
            sta = '%s+' % sta
        return sta

    def ircCommandJOIN(self, jid):
        """IRC command join channel

        @type jid: JID
        @param jid: The MUC JID for which to generate a JOIN message
        """
        nick = self.makeNickFromJID(jid, True)
        channel = jid.getStripped()
        msg = ':%s!%s JOIN :#%s' % (
            nick,
            self.makeHostFromJID(jid),
            self.fixChannel(channel))
        self.sendToIRC(msg)

        role = self.mucs[channel][jid]['role']
        args = ''
        if role == 'moderator':
            args = '+o'
        elif role == 'participant':
            args = '+v'
        if args:
            self.ircCommandMODEMUCUSER(JID(channel), jid, args)

    def ircCommandSELFJOIN(self, room_jid):
        """IRC command join channel

        @type room_jid: JID
        @param room_jid: JID of the room we want to join
        """
        snick = self.nickname
        lines = list()
        channel = self.fixChannel(room_jid)
        lines.append(':%s JOIN :#%s'% (snick, channel))
        lines.append(':%s MODE #%s +n' % (self.server, channel))
        
        for jid in self.mucs[room_jid].iterkeys():
            nick = snick
            if (jid.getResource() != nick):
                nick = self.makeNickFromJID(jid, True)
            if self.mucs[room_jid][jid]['role'] == 'moderator':
                nick = "@%s" % nick
            elif self.mucs[room_jid][jid]['role'] == 'participant':
                nick = "+%s" % nick
            lines.append(':%s 353 %s = #%s :%s' % (self.server, snick, channel, nick))
        lines.append(':%s 366 %s #%s :End of /NAMES list.'% (self.server, snick, channel))
        while lines:
            msg = lines.pop(0)
            self.sendToIRC(msg)

    def ircCommandPART(self, jid, text):
        """IRC command part channel

        @type jid: JID
        @type text: string
        @param jid: The MUC JID for which to generate a PART message
        @param text: the part message
        """
        nick = self.makeNickFromJID(jid, True)
        msg = ':%s!%s PART #%s :%s' % (
            nick,
            self.makeHostFromJID(jid),
            self.fixChannel(jid.getStripped()),
            text)
        self.sendToIRC(msg)
        
    def ircCommandNICK(self, old_jid, new_jid):
        """Reports a nick change to the IRC client
        
        @type old_jid: JID
        @type new_jid: JID
        @param old_jid: JID before the nick change
        @param new_jid: JID after the nick change
        """
        
        msg = ':%s!%s NICK :%s' % (
            self.makeNickFromJID(old_jid, True),
            self.makeHostFromJID(old_jid),
            self.makeNickFromJID(new_jid, True))
        self.sendToIRC(msg)

    def ircCommandPRIVMSG(self, jid, is_muc, is_private, text, timestamp=''):
        """Converts private messages to IRC client

        @type jid: JID
        @type is_muc: boolean
        @type text: string
        @type timestamp: string
        @param jid: the JID from which the mesage was sent
        @param is_muc: whether the message was sent in a muc
        @param text: the message
        @param timestamp: timestamp of the message
        """
        nick = self.makeNickFromJID(jid, is_muc)
        lines = text.splitlines()
        messages = list()
        for line in lines:
            action = False
            if line.upper().startswith('/ME '):
                line = line[4:]
                action = True
            if timestamp:
                line = "[%s] %s " % (timestamp, line)
            if action:
                line = self.makeIRCACTION(line)

            if is_muc and not is_private:
                msg = ':%s!%s PRIVMSG #%s :%s' % (nick, self.makeHostFromJID(jid), self.fixChannel(jid.getStripped()), line)
            else:
                msg = ':%s!%s PRIVMSG %s :%s' % (nick, self.makeHostFromJID(jid), self.nickname,line)
            messages.append(msg)
        for msg in messages:
            self.sendToIRC(msg)

    def ircCommandTOPIC(self, jid, topic):
        """Converts MUC topic to IRC channel topic

        @type jid: JID
        @type topic: string
        @param jid: The jid who initiated the topic change
        @param topic: the topic
        """
        nick = self.makeNickFromJID(jid, True)
        msg =':%s!%s TOPIC #%s :%s' % (nick, self.makeHostFromJID(jid), self.fixChannel(jid.getStripped()), topic)
        self.sendToIRC(msg)

    def ircCommandMODEMUC(self, room_jid, args):
        """Converts MUC mode to IRC channel mode

        @type room_jid: JID
        @type args: string
        @param room_jid: JID of the room
        @param args: arguments of the mode
        """
        nick = self.nickname
        channel = self.fixChannel(room_jid)
        msg = ':%s 324 %s #%s %s' % (self.server, nick, channel, args)
        self.sendToIRC(msg)
        msg = ':%s 329 %s #%s %s' % (self.server, nick, channel, '1031538353')
        self.sendToIRC(msg)

    def ircCommandMODEMUCBANLIST(self, room_jid):
        """Converts MUC ban list to IRC channel banlist, is unfinished

        @type room_jid: JID
        @param room_jid: JID of the room
        """
        nick = self.nickname
        msg = ':%s 368 %s #%s :End of Channel Ban List' % (self.server, nick, self.fixChannel(room_jid))
        self.sendToIRC(msg)

    def ircCommandMODEMUCUSER(self, giver, taker, args):
        """Convers MUC user mode to IRC channel user mode. Example use cases
        are when someone is granted a voice or admin rights on a MUC.

        @type giver: JID
        @type taker: JID
        @type args: string
        @param giver: The user initiating the mode change
        @param taker: The user affected by the mode change
        @param args: arguments of the mode
        """
        msg = ':%s!%s MODE #%s %s %s' % (self.makeNickFromJID(giver, True),
                                         self.makeHostFromJID(giver),
                                         self.fixChannel(taker.getStripped()),
                                         args,
                                         self.makeNickFromJID(taker, True))
        self.sendToIRC(msg)

    def ircCommandMODE(self, args):
        """Converts XMPP mode to IRC mode. Unfinished

        @type args: string
        @param args: arguments of the mode
        """
        # just to keep irssi happy, fix later to more meaningfull
        msg = ':%s MODE %s :%s' % (self.nickname, self.nickname, args)
        self.sendToIRC(msg)

    def ircCommandERROR(self, message='', ircerror=0):
        """Convert XMPP errors to IRC errors

        @type message: string
        @type ircerror: integer
        @param message: the error message
        @param ircerror: number of the error
        """
        msg = ''
        if ircerror == 0:
            msg = 'ERROR :XMPP ERROR %s' % message
        elif ircerror == -1:
            msg = 'ERROR :xmpp-ircd error %s' % message
        elif ircerror == 403:
            msg = ':%s 403 %s %s :That channel doesn\'t exist' % (self.server, self.nickname, self.server)
        elif ircerror == 464:
            msg = ':%s 464 :Password incorrect' % (self.server)
        self.sendToIRC(msg)

    def ircCommandERRORMUC(self, number, errormess, room):
        """Convert XMPP MUC errors to IRC channel errors

        @type number: integer
        @type errormess: string
        @type room: string
        @param number: number of the error
        @param errormess: the error message
        @param room: the MUC
        """
        text = ''
        if number == 403:
            text = 'No such channel'
        elif number == 404:
            text = 'Cannot send to channel'
        elif number == 467:
            text = 'Channel key already set'
        elif number == 471:
            text = 'Cannot join channel (+l)'
        elif number == 473:
            text = 'Cannot join channel (+i)'
        elif number == 474:
            text = 'Cannot join channel (+b)'
        elif number == 475:
            text = 'Cannot join channel (+k)'
        elif number == 476:
            text = 'Bad Channel Mask'
        elif number == 477:
            text = 'Channel doesn\'t support modes'
        elif number == 478:
            text = 'Channel list is full'
        elif number == 481:
            text = 'Permission Denied- You\'re not an IRC operator'
        elif number == 482:
            text = 'You\'re not channel operator'
        else:
            text = 'No text'

        msg = ':%s %s %s #%s :%s' % (
            self.server, number, self.nickname, self.fixChannel(room), text)
        self.sendToIRC(msg)
        self.ircCommandERROR(errormess)

    def ircCommandWHO(self, users, room_jid):
        """Convert XMPP vcard query to IRC who

        @type users: list
        @type room_jid: JID
        @param users: list of users
        @param room_jid: the JID of the room
        """
        channel = self.fixChannel(room_jid)
        for user in users:
            nick = self.makeNickFromJID(user, True),
            msg = ':%s 352 %s #%s %s %s %s %s %s :0 %s' % (
                self.server,
                self.nickname,
                channel,
                user.getResource(),
                user.getDomain(),
                self.server,
                nick,
                self.ircGetStatus(user, room_jid),
                self.fixNick(user.getResource()))
            self.sendToIRC(msg)

        msg = ':%s 315 %s #%s :End of /WHO list.' % (self.server, self.nickname, channel)
        self.sendToIRC(msg)

    def ircCommandWHOIS(self, jid):
        """Convert XMPP vcard query to IRC whois

        @type jid: JID
        @param jid: Jabber id of the user whose vcard is quered
        """
        nick = self.makeNickFromJID(jid, self.mucs.has_key(jid.getStripped()))
        lines = [
            ':%s 311 %s %s %s %s * : %s' % (
                self.server,
                self.nickname,
                nick,
                jid.getNode(),
                jid.getDomain(),
                nick),
            ':%s 312 %s %s %s : XMPP xmpp-ircd' % (self.server, self.nickname, nick, self.server),
            ':%s 318 %s %s :End of /WHOIS list.' % (self.server, self.nickname, nick)]
        while lines:
            self.sendToIRC(lines.pop(0))

    def ircCommandLIST(self, channels):
        """Convert XMPP query to IRC channel list

        @type channels: list
        @param channels: list of channels
        """
        msg = ':%s 321 %s Channel :Users Name' % (self.server, self.nickname)
        self.sendToIRC(msg)

        for channel in channels:
            # todo: implement # visible and topic
            msg = ':%s 322 %s #%s 5 :Unknown' % (
                self.server,
                self.nickname,
                channel)
            self.sendToIRC(msg)

        msg = ':%s 323 %s :End of /LIST' % (self.server, self.nickname)
        self.sendToIRC(msg)

    def ircCommandUNAWAY(self):
        """Convert XMPP status to IRC away"""
        nick = self.nickname
        msg = ':%s 305 %s :%s' % (
            self.server,
            nick,
            'You are no longer marked as being away')
        self.sendToIRC(msg)

    def ircCommandNOWAWAY(self):
        """Convert XMPP status to IRC not away"""
        nick = self.nickname
        msg = ':%s 306 %s :%s' % (
            self.server,
            nick,
            'You have been marked as being away')
        self.sendToIRC(msg)

    def ircCommandNOTICE(self, text):
        """Convert XMPP message to IRC notice

        @type text: string
        @param text: notice text
        """
        nick = self.nickname
        msg = 'NOTICE %s :%s' % (nick, text)
        self.sendToIRC(msg)

    def xmppCommandMUCMODE(self, jid):
        """Send XMPP MUC mode change

        @type jid: string
        @param jid: Jabber id of the MUC
        """
        iq = protocol.Iq(to=jid,
                         queryNS=NS_DISCO_INFO,
                         typ = 'get')
        iq.setID('disco3')
        self.sendToXMPP(iq)

    def xmppCommandMUCUSERS(self, jid):
        """Send XMPP MUC users query

        @type jid: string
        @param jid: Jabber id of the MUC
        """
        iq = protocol.Iq(to=jid,
                         queryNS=NS_DISCO_ITEMS,
                         typ = 'get')
        iq.setID('disco_muc_users')
        self.sendToXMPP(iq)

    def xmppCommandMUCROOMS(self):
        """Send XMPP MUC rooms query
        """
        iq = protocol.Iq(to=self.muc_server,
                         queryNS=NS_DISCO_ITEMS,
                         typ = 'get')
        iq.setID('disco_muc_rooms')
        self.sendToXMPP(iq)

    def xmppCommandSTATUS(self, show, status):
        """Send XMPP status change

        @type show: string
        @type status: string
        @param show: status
        @param status: status
        """
        for muc in self.mucs.keys():
            p=Presence(to='%s/%s' % (
                       muc,
                       self.nickname))
            if not show == '':
                p.setShow(show)
            p.setStatus(status)
            self.sendToXMPP(p)
        p=Presence()
        p.setShow(show)
        p.setStatus(status)
        self.sendToXMPP(p)
        if show.upper() in STATUSSTATES[2:]: # available, chat
            self.ircCommandNOWAWAY()
        else:
            self.ircCommandUNAWAY()

    def xmppCommandMUCPRESENCE(self, muc, nick):
        """Send XMPP presence to MUC room

        @type muc: string
        @type nick: string
        @param muc: Jabber ID of the MUC
        @param nick: users nickname for the MUC
        """
        self.sendToXMPP(Presence(to='%s/%s' %
                                 (muc,
                                  nick)))

    def xmppCommandMUCROLE(self, muc, nick, role):
        """Send XMPP MUC role to MUC room

        @type muc: string
        @type nick: string
        @type role: role
        @param muc: Jabber ID of the MUC
        @param nick: users nickname in the MUC
        @param role: role of the user
        """
        iq = protocol.Iq(to=muc,
                         queryNS=NS_MUC_ADMIN,
                         typ = 'set')
        item = iq.getTag('query').setTag('item')
        item.setAttr('nick', nick)
        item.setAttr('role', role)
        self.sendToXMPP(iq)

    def xmppCommandGETWHOIS(self, jid):
        """Send XMPP vcard, last activity and sofware version request for some
        Jabber ID.

        @type jid: string
        @param jid: Jabber ID
        """
        # vcard
        iq = protocol.Iq(to=jid,
                         typ = 'get')
        iq.setTag(NS_VCARD + ' vCard')
        iq.setID('v3')
        self.sendToXMPP(iq)

        # last activity
        iq = protocol.Iq(to=jid,
                         typ = 'get',
                         queryNS=NS_LAST)
        self.sendToXMPP(iq)

        # software version
        # todo: looks at roster which doesn't exist, fix this
        if not jid.getResource():
            # try to find match in roster
            rosternicks = self.mucs['roster'].keys()
            for x in rosternicks:
                if x.getStripped() == jid.getStripped():
                    jid = x
            if not  jid.getResource():
                return
        iq = protocol.Iq(to=jid,
                         typ = 'get',
                         queryNS=NS_VERSION)
        self.sendToXMPP(iq)

    def xmppCommandINFOGET(self, jid):
        """Not finished """
        pass

    def xmppCommandSOFTWAREVERSION(self, jid):
        """Send set software version XMPP. Not finished """
        pass

    def xmppCommandLASTACTIVITY(self, jid):
        """Send last activity XMPP. Not finished """
        pass

    def run(self):
        self.component.registerJid(self)

        while self.connected and self.nickname is None:
            try:
                data = self.socket.recv(4096)
            except:
                self.printError('Not receiving enough data from socket')
                self.connected = False
            if data:
                for line in data.splitlines():
                    self.commandHandler(line)
            else:
                self.connected = False

        if self.connected:
            nick = self.nickname
            lines = ["NOTICE AUTH :*** Looking up your hostname...",
                     "NOTICE AUTH :*** Found your hostname, welcome back",
                     "NOTICE AUTH :*** Checking ident",
                     "NOTICE AUTH :*** No identd (auth) response",
                     ":%s 001 %s :Welcome to xmpp-ircd, IRC to XMPP gateway %s!%s" %
                         (self.server, nick, nick, self.makeHostFromJID(self.JID)),
                     ":%s 002 %s :Your host is %s [%s port %s] running version xmpp-ircd-%s" % (
                         self.server,
                         nick,
                         self.server,
                         self.server,
                         self.port,
                         XMPPIRCDVERSION),
                     ":%s 003 %s :This server was created %s" % (self.server, nick, self.component.startup_time),
                     ":%s 004 %s :%s xmpp-ircd%s spmAFkPBaTuUovbn q" % (self.server, nick, self.server, XMPPIRCDVERSION)
                     ]
            while lines:
                self.sendToIRC(lines.pop(0))

        """Here is this threads main functionality. Jabber-thread is started
        and polling of socket for IRC-messages is in here."""
        jt = self.component.jt

        while self.connected and jt.connected:
            try:
                data = self.socket.recv(4096)
            except:
                self.printError('Not receiving enough data from socket')
                self.connected = False
            if data.find ( 'PING' ) != -1:
                # check that our rooms are still alive
                if self.pingCounter == 5:
                    self.pingCounter = 0
                    for muc in self.mucs.keys():
                        if self.disconnectedMucs.has_key(muc):
                            if self.disconnectedMucs[muc] < 5:
                                self.disconnectedMucs[muc] = self.disconnectedMucs[muc] + 1
                            else:
                                self.disconnectedMucs[muc] = 0
                                self.roomPingQueue[muc] = ''
                                self.xmppCommandMUCMODE(muc)
                        else:
                            self.roomPingQueue[muc] = ''
                            self.xmppCommandMUCMODE(muc)
                else:
                    self.pingCounter += 1
                self.sendToIRC('PONG %s' % (self.server))
            if data:
                for line in data.splitlines():
                    self.commandHandler(line)
            else:
                self.connected = False
        if jt.connected:
            # leave all rooms
            for room in self.mucs.keys():
                self.sendToXMPP(Presence(to='%s/%s' % (room, self.nickname),
                                     typ='unavailable',
                                     status=''))
        else:
            self.ircCommandNOTICE('XMPP server disconnected, shutting down xmpp-ircd.')
        self.component.unregisterJid(self)
        try:
            self.socket.shutdown(socket.SHUT_RDWR)
        except socket.error:
            self.printError('Socket shutdown client')
        self.socket.close()

    def messageHandlerError(self, sess, mess):
        """Handle incoming error messages from XMPP

        @type sess: string
        @type mess: Message
        @param sess: session
        @param mess: error message

        """
        text = ''
        try:
            text = mess.getTag('error').getTag('text').getData()
        except:
            pass
        erc = mess.getErrorCode()
        jidFrom = mess.getFrom()
        to = mess.getTo()
        if erc == '403':
            self.ircCommandERRORMUC(482, text, jidFrom)
        else:
            self.printDebug('MUC ERROR NOT IMPLEMENTED')

    def messageHandler(self, sess, mess):
        """Handle incoming XMPP with type message

        @type sess: string
        @type mess: Message
        @param sess: session
        @param mess: XMPP Message
        """
        if mess.getType() == 'error':
            self.messageHandlerError(sess,mess)
            return

        jid = mess.getFrom()
        text = mess.getBody()
        topic = mess.getSubject()
        
        ts = ''
        if mess.getTag('x',namespace=NS_DELAY):
            ts=mess.getTimestamp()
            if not ts:
                ts=mess.setTimestamp()
                ts=mess.getTimestamp()
            ts = time.strptime(ts,'%Y%m%dT%H:%M:%S')
            ts = datetime.datetime(*ts[:-3])
        if not text and not topic:
            return

        private = True
        if mess.getType() == 'groupchat':
            private = False
        
        MUC = self.mucs.has_key(jid.getStripped())

        if private:
            self.ircCommandPRIVMSG(jid, MUC, True, text, ts)
        elif topic:
            self.ircCommandTOPIC(jid, topic)
        elif not jid.getResource() == self.nickname or ts:
            self.ircCommandPRIVMSG(jid, True, False, text, ts)


    def iqHandler(self, con, iq):
        """Handle incoming XMPP with type Iq

        @type con: Connection
        @type iq: Iq
        @param con: XMPP Connection
        @param iq: XMPP Iq
        """
        ns = iq.getQueryNS()
        if ns is None:
            ns = iq.getProperties()[0]

        if ns == NS_DISCO and iq.getType() in ['get', 'error']:
            self.iqHandlerInfo(con, iq)
        elif ns == NS_DISCO_ITEMS and iq.getType() == 'result':
            self.iqHandlerItems(con, iq)
        elif ns == NS_DISCO_INFO and iq.getType() == 'result':
            self.iqHandlerInfo(con, iq)
        elif ns == NS_DISCO_INFO and iq.getType() == 'get':
            self.xmppCommandINFOGET(iq.getFrom())
        elif ns == NS_DISCO_ITEMS and iq.getType() == 'error':
            self.iqHandlerError(con, iq)
        elif ns == NS_DISCO_INFO and iq.getType() == 'error':
            self.iqHandlerError(con, iq)
        elif ns == NS_VCARD and iq.getType() == 'result':
            self.iqHandlerVcard(con, iq)
        elif ns == NS_VCARD and iq.getType() == 'error':
            self.iqHandlerVcardError(con, iq)
        elif ns == NS_LAST and iq.getType() == 'result':
            self.iqHandlerLast(con, iq)
        elif ns == NS_LAST and iq.getType() == 'get':
            self.xmppCommandLASTACTIVITY(iq.getFrom())
        elif ns == NS_LAST and iq.getType() == 'error':
            self.iqHandlerLastError(con, iq)
        elif ns == NS_VERSION and iq.getType() == 'result':
            self.iqHandlerVersion(con, iq)
        elif ns == NS_VERSION and iq.getType() == 'error':
            self.iqHandlerVersionError(con, iq)
        elif ns == NS_VERSION and iq.getType() == 'get':
            self.xmppCommandSOFTWAREVERSION(iq.getFrom())
        else:
            self.printDebug('IQ HANDLER FOR THIS NAMESPACE NOT IMPLEMENTED YET')


    def iqHandlerLastError(self,con, iq):
        """Handle incoming XMPP with type Iq and last activity error

        @type con: Connection
        @type iq: Iq
        @param con: XMPP Connection
        @param iq: XMPP Iq
        """
        self.printDebug('iqHandlerLastError')

    def iqHandlerLast(self, con, iq):
        """Handle incoming XMPP with type Iq and last activity

        @type con: Connection
        @type iq: Iq
        @param con: XMPP Connection
        @param iq: XMPP Iq
        """
        ch = iq.getTag('query')
        seconds = ch.getAttr('seconds')
        self.ircCommandNOTICE('** Last active information for %s **' % iq.getFrom())
        self.ircCommandNOTICE('Idle %s second**' % seconds)

    def iqHandlerVersionError(self,con, iq):
        """Handle incoming XMPP with type Iq and version error

        @type con: Connection
        @type iq: Iq
        @param con: XMPP Connection
        @param iq: XMPP Iq
        """
        self.printDebug('iqHandlerVersionError')

    def iqHandlerVersion(self, con, iq):
        """Handle incoming XMPP with type Iq and version

        @type con: Connection
        @type iq: Iq
        @param con: XMPP Connection
        @param iq: XMPP Iq
        """
        ch = iq.getTag('query').getChildren()
        self.ircCommandNOTICE('** Software version information for %s **' % iq.getFrom())
        for c in ch:
            self.ircCommandNOTICE('%s: %s' % (c.getName(), c.getData()))

    def iqHandlerVcardError(self,con, iq):
        """Handle incoming XMPP with type Iq and vcard error

        @type con: Connection
        @type iq: Iq
        @param con: XMPP Connection
        @param iq: XMPP Iq
        """
        self.printDebug('iqHandlerVcardError')

    def iqHandlerVcard(self, con, iq):
        """Handle incoming XMPP with type Iq and vcard

        @type con: Connection
        @type iq: Iq
        @param con: XMPP Connection
        @param iq: XMPP Iq
        """
        card = {}
        ch = iq.getTag('vCard').getChildren()
        for c in ch:
            name = c.getName()
            if name != 'PHOTO':
                if name == 'EMAIL':
                    emailv = c.getChildren()
                    card['EMAIL %s' % emailv[0].getName()] = emailv[1].getData()
                card[c.getName()] = c.getData()

        self.ircCommandNOTICE('** Vcard information for %s **' % iq.getFrom())
        for key in card.keys():
            for line in card[key].splitlines():
                self.ircCommandNOTICE('%s: %s' % (key, line))

    def iqHandlerError(self, con , iq):
        """Handle incoming XMPP with type Iq and error

        @type con: Connection
        @type iq: Iq
        @param con: XMPP Connection
        @param iq: XMPP Iq
        """
        jid = iq.getFrom()
        # this can mess things up, fix later
        if self.roomPingQueue.has_key(jid):
            del (self.roomPingQueue[jid])
        # room errors
        errornum = iq.getErrorCode()
        if jid in self.mucs.keys():
            if errornum == '404' and jid not in self.disconnectedMucs.keys():
                self.ircCommandERRORMUC(404, 'MUC DISCONNECTED', jid)
                self.ircCommandPRIVMSG(JID("%s/%s" % (jid, 'telepaatti')),
                                       True,
                                       False,
                                       'MUC IS DISCONNECTED YOUR TEXT WILL NOT SHOW ON CHANNEL. YOU CAN WAIT UNTIL MUC CONNECTS AGAIN OR USE /PART TO LEAVE THIS MUC!',
                                       timestamp='')
                self.disconnectedMucs[jid] = 0
                return
            else:
                return
        else:
            self.ircCommandERROR('iq error num %s jid not room! jid %s' % (errornum, jid))


    def iqHandlerItems(self, con, iq):
        """Handle incoming XMPP with type Iq and items

        @type con: Connection
        @type iq: Iq
        @param con: XMPP Connection
        @param iq: XMPP Iq
        """

        jid = iq.getFrom()
        if iq.getType() == 'error':
            # some fixing is needed
            self.ircCommandERROR('%s %s ' % (iq.getErrorCode(), iq.getErrorCode()))
        elif iq.getID() == 'disco_muc_users':
            ch = iq.getQueryChildren()
            mucusers = list()
            for c in ch:
                name = c.getName()
                if name == 'item':
                    mucusers.append(JID(c.getAttrs()['jid']))
            if self.mucs.has_key(jid):
                pass # we keep track of users else where
            self.ircCommandWHO(mucusers, jid)
            return
        elif iq.getID() == 'disco_muc_rooms':
            ch = iq.getQueryChildren()
            channels = list()
            for c in ch:
                name = c.getName()
                if name == 'item':
                    channels.append(self.fixChannel(c.getAttrs()['jid']))
            self.ircCommandLIST(channels)
            return
        else:
            self.printDebug('UNKNOWN DISCO ITEM %s ' % jid)

    def iqHandlerInfo(self, con, iq):
        """Handle incoming XMPP with type Iq and info

        @type con: Connection
        @type iq: Iq
        @param con: XMPP Connection
        @param iq: XMPP Iq
        """
        roomname = iq.getFrom()
        # this can mess things up, fix
        if self.roomPingQueue.has_key(roomname):
            del (self.roomPingQueue[roomname])
            return

        MUC = False
        roomfeats = list()
        if iq.getType() == 'error':
            # fix this later
            self.ircCommandERROR('%s %s ' % (iq.getErrorCode(), iq.getErrorCode()))
        else:
            ch = iq.getQueryChildren()
            for c in ch:
                name = c.getName()
                if name == 'identity':
                    atrs = c.getAttrs()
                    if atrs.has_key('type') and atrs.has_key('category'):
                        if atrs['type'] == 'text' and \
                                atrs['category'] == 'conference':
                            MUC = True
                elif name == 'var':
                    roomfeats.append(c.getAttrs()['var'])
                elif name == 'feature':
                    roomfeats.append(c.getAttrs()['var'])
                else:
                    self.printDebug('%s NOT IMPLeMENTED' % name)
            if MUC: # for MODE
                modestr = '+'
                for feat in roomfeats:
                    if feat == 'muc_hidden':
                        modestr += 's'
                    if feat == 'muc_membersonly':
                        modestr += 'p'
                    if feat == 'muc_moderated':
                        modestr += 'm'
                    if feat == 'muc_nonanonymous':
                        modestr += 'A'
                    if feat == 'muc_open':
                        modestr += 'F'
                    if feat == 'muc_passwordprotected':
                        modestr += 'k'
                    if feat == 'muc_persistent':
                        modestr += 'P'
                    if feat == 'muc_public':
                        modestr += 'B'
                    if feat == 'muc_rooms':
                        self.printDebug('muc_rooms not implemented')
                    if feat == 'muc_semianonymous':
                        modestr += 'a'
                    if feat == 'muc_temporary':
                        modestr += 'T'
                    if feat == 'muc_unmoderated':
                        modestr += 'u'
                    if feat == 'muc_unsecured':
                        modestr += 'U'
                self.ircCommandMODEMUC(roomname, modestr)
            else:
                self.printDebug("IQ stuff still missing here")

    def presenceHandler(self, sess, pres):
        """Handle incoming XMPP with type presence

        @type sess: Connection
        @type pres: Presence
        @param sess: XMPP Connection
        @param press: XMPP Presence
        """
        MUC = False
        ptype = pres.getType()
        nick=pres.getFrom()
        tags = pres.getTags('x')
        for tag in tags:
            ns = tag.getNamespace()
            if ns.startswith(NS_MUC):
                MUC = True

        if not MUC:
            self.printDebug('non-muc presence somehow? investigate...')
            return

        role = pres.getRole()
        affiliation = pres.getAffiliation()
        show = pres.getShow()
        status = pres.getStatus()

        room = JID(pres.getFrom().getStripped())

        # for affiliation and role changes
        if self.mucs.has_key(room) and \
                nick in self.mucs[room].keys():
            xrole = self.mucs[room][nick]['role']
            xaffiliation = self.mucs[room][nick]['affiliation']
            if role != xrole: # role has changed
                giver = JID('%s/telepaatti' % room)
                if role.upper() == 'MODERATOR':
                    self.ircCommandMODEMUCUSER(giver, nick, '+o')
                    self.ircCommandMODEMUCUSER(giver, nick, '-v')
                if role.upper() == 'PARTICIPANT':
                    self.ircCommandMODEMUCUSER(giver, nick, '-o')
                    self.ircCommandMODEMUCUSER(giver, nick, '+v')
                if role.upper() == 'VISITOR':
                    self.ircCommandMODEMUCUSER(giver, nick, '-o')
                    self.ircCommandMODEMUCUSER(giver, nick, '-v')
                else:
                    self.printDebug('MODE NONE')
            if xaffiliation != affiliation: # affiliation has changed
                pass

        # for nick changes
        if (pres.getNick() == self.newnick or pres.getNick() == self.nickname)\
                and pres.getStatusCode() == '303':
            self.nickChangeInMucs[room] = {'checked': True,
                                           'changed': True}
            # check if we have checked all MUCs
            for muc in self.nickChangeInMucs.keys():
                if not self.nickChangeInMucs[muc]['checked']:
                    return # no need to go any further
            # check if it changed in all MUCs
            for muc in self.nickChangeInMucs.keys():
                if not self.nickChangeInMucs[muc]['changed']:
                    changedMucs = list()
                    for gei in self.nickChangeInMucs.keys():
                        if self.nickChangeInMucs[gei]['changed']:
                            changedMucs.append(gei)
                    self.nickChangeInMucs = {}
                    for muc2 in changedMucs:
                        self.nickChangeInMucs[muc2] = {'checked': False,
                                                       'changed': False}
                        self.xmppCommandMUCPRESENCE(muc2, self.nickname)
                    self.ircCommandERROR('Nick conflicts in some MUC wont change')
                    return # out
            # remove, all have changed
            self.nickChangeInMucs = {}
            if pres.getNick() == self.nickname:
                self.newnick = ''
                return
            self.sendToIRC(':%s NICK :%s' %
                           (self.nickname,
                            self.newnick))
            for muc in self.getMucs():
                del (self.mucs[muc][JID("%s/%s" % (muc, self.nickname))]) # remove the old
                # add the new
                self.mucs[muc][JID("%s/%s" % (muc, self.newnick))] = { 'role': role,
                                                                       'affiliation': affiliation }
            self.nickname = self.newnick
            self.newnick = ''
            return

        if ptype == 'error':
            er = pres.getError()
            erc = pres.getErrorCode()
            if erc == '401':
                self.ircCommandERRORMUC(475, 'Password requeired to join', room)
            elif erc == '403':
                self.ircCommandERRORMUC(474, 'Cannot join MUC, you are banned', room)
            elif erc == '404':
                self.ircCommandERRORMUC(404, 'No such MUC', room)
            elif erc == '405':
                self.ircCommandERRORMUC(478, 'Can\'t create MUC', room)
            elif erc == '406':
                self.ircCommandERRORMUC(437, 'You must use reserverd nick to enter', room)
            elif erc == '407':
                self.ircCommandERRORMUC(473, 'Must be a member to enter', room)
            elif erc == '409':
                self.ircCommandERRORMUC(437, 'You must change nickname to enter', room)
            elif erc == '503':
                self.ircCommandERRORMUC(471, 'MUC is full', room)
            else:
                self.ircCommandERROR('MUC error not yet implemented (%d %s)' % (erc, er))
        else:
            joining = self.joinQueue.has_key(room)
            inroom = self.mucs.has_key(room)
            if ptype == 'unavailable':
                self.printDebug('unavailable')
                if nick.getResource() == self.nickname:
                    self.printDebug('our self')
                    if joining:
                        del (self.joinQueue[room])
                    elif self.nickChangeInMucs.has_key(room):
                        # between nick change
                        self.printDebug('we are between nick change')
                        return
                    elif inroom:
                        self.ircCommandPART(nick, ' left')
                        if nick in self.mucs[room].keys():
                            del (self.mucs[room])
                    else:
                        line = "%s is doing something" % nick
                        self.printDebug(line.encode('utf-8'))
                else: # someonerin else
                    if joining:
                        self.printDebug("%s left while we are joining room %s" % (
                            nick, room))
                    elif inroom:
                        if pres.getStatusCode() == '303':
                            self.changingNick[JID("%s/%s" % (nick.getStripped(), pres.getNick()))] = nick
                        else:
                            self.ircCommandPART(nick, 'left')

                        del (self.mucs[room][nick])
                    else:
                        line = "%s is doing something" % nick
                        self.printDebug(line.encode('utf-8'))
            else: # not unavailable type
                self.printDebug('not unavailable')
                if nick.getResource() == self.nickname:
                    if joining:
                        # fix this also later
                        self.mucs[room] = self.joinQueue[room]['users']
                        self.mucs[room][JID("%s/%s" % (room, self.nickname))] = { 'role': role,
                                                                                  'affiliation': affiliation,
                                                                                  'show' : show,
                                                                                  'status': status}
                        del(self.joinQueue[room])
                        self.ircCommandSELFJOIN(room)
                    elif inroom:
                        self.mucs[room][JID("%s/%s" % (room, self.nickname))] = { 'role': role,
                                                                                  'affiliation': affiliation,
                                                                                  'show' : show,
                                                                                  'status': status}
                    else:
                        line = "%s is doing something" % nick
                        self.printDebug(line.encode('utf-8'))
                elif nick.getResource() == self.newnick:
                    pass
                else: # someone else
                    if joining:
                        if nick not in self.joinQueue[room]['users'].keys():
                            self.joinQueue[room]['users'][nick] = { 'role': role,
                                                                    'affiliation': affiliation,
                                                                    'show' : show,
                                                                    'status': status}
                    elif inroom:
                        new_user = nick not in self.mucs[room].keys()
                        self.mucs[room][nick] = { 'role': role,
                                      'affiliation': affiliation,
                                      'show' : show,
                                      'status': status }
                        if nick in self.changingNick.keys():
                            self.ircCommandNICK(self.changingNick[nick], nick)
                        elif new_user:
                            self.ircCommandJOIN(nick)
                    else:
                        self.printDebug('TROUBLE LINE')

    def commandHandler(self, data):
        """Command handler for commands and text coming in from IRC-client

        @type data: string
        @param data: IRC data coming from IRC-client
        """
        self.printDebug('got ircline: %s' % data)
        # utf-8 test
        try:
            unicode(data, 'utf-8')
        except exceptions.UnicodeDecodeError:
            self.printError('Unicode decode error. Your IRC client is (probably) not writing utf-8')
            self.ircCommandERROR('Input form IRC client was not in utf-8. Turn utf-8 support on from your IRC client or input only pure ascii',-1)
            return

        args = data.split(' ', 1)
        arguments = u''
        command = args[0].upper()
        if len(args) == 2:
            arguments = args[1]
        arguments = arguments.strip()
        MUC = arguments.startswith('#')
        if MUC:
            arguments = self.fixChannelCommand(arguments)
            
        if self.nickname is None:
            if command == 'NICK':
                nick = ''
                if arguments[0] == ':':
                    nick = arguments[1:]
                else:
                    nick = arguments

                self.nickname = self.fixNick(nick)
                
            elif command == 'PASS':
                if arguments[0] == ':':
                    self.passwd = arguments[1:]
                else:
                    self.passwd = arguments

            return

        if command == 'JOIN':
            #We won't be able to join rooms with a space in their name, but it's not as bad as being unable to join rooms with a password
            arguments = arguments.split(' ', 1)
            room = arguments[0]
            password = u''
            if len(arguments) == 2:
                password = arguments[1]

            if self.fullRoomJid and not room.endswith("@%s" % self.muc_server):
                room = "%s@%s" % (room, self.muc_server)

            room = room.lower() # todo: is this valid?
            if room in self.mucs.keys(): # already in MUC
                return
            self.printDebug("Joining room: %s" % JID(room))
            self.joinQueue[JID(room)] = {'messages': list(),
                            'users': {}}
            p=Presence(to='%s/%s' % (
                    room,
                    self.nickname))
            p.setTag('x',namespace=NS_MUC).setTagData('password', password)
            p.getTag('x').addChild('history',{'maxchars':'10000','maxstanzas':'100'})
            self.sendToXMPP(p)

        elif command == 'PART':
            x = arguments.find(' :')
            text = ''
            room = ''
            if x > 0:
                text = arguments[x+2:]
                text = text.strip()
                room = arguments[:x]
                room = JID(room.strip())
            else:
                room = JID(arguments.strip())
            if room not in self.mucs.keys(): # not in room
                return
            self.sendToXMPP(Presence(to='%s/%s' % (room, self.nickname),
                                     typ='unavailable',
                                     status=text))

        elif command == 'PRIVMSG':
            x = arguments.find(' :')
            text = ''
            nick = ''
            if x > 0:
                text = arguments[x+2:]
                text = text.strip()
                sact = text.find('\001ACTION ')
                eact = text.rfind('\001')
                if sact > -1 and eact > -1:
                    text = '/me %s' % text[sact+8:eact]
                nick = arguments[:x]
                nick = nick.strip()
            type = 'chat'
            if MUC:
                type = 'groupchat'
            
            jid = self.getJIDFromNick(nick)
            if jid is None:
                return
            
            self.sendToXMPP(protocol.Message(jid,
                             text,
                             typ = type))

        elif command == 'NICK':
            if arguments[0] == ':':
                self.newnick = self.fixNick(arguments[1:])
            else:
                self.newnick = self.fixNick(arguments)
                
            if self.newnick == self.nickname:
                self.newnick = ''
                return
                
            if len(self.getMucs()) == 0:
                self.sendToIRC(':%s NICK :%s' %
                               (self.nickname,
                                self.newnick))
                self.nickname=self.newnick
                self.newnick = ''
                
            for muc in self.getMucs():
                self.nickChangeInMucs[muc] = {'checked': False,
                                'changed': False}
            for muc in self.nickChangeInMucs.keys():
                self.xmppCommandMUCPRESENCE(muc, self.newnick)

        elif command == 'TOPIC':
            x = arguments.find(' :')
            text = ''
            jid = None
            if x > 0:
                text = arguments[x+2:]
                text = text.strip()
                jid = JID(arguments[:x].strip())
            if jid not in self.mucs.keys():
                self.ircCommandERROR('', 403)
                return

            self.sendToXMPP(protocol.Message(jid,
                             typ = 'groupchat',
                             subject = text))

        elif command == 'MODE':
            if not arguments:
                return
            arguments = arguments.split(' ', 2)
            params = ''
            nick = arguments[0]
                
            if len(arguments) >= 2:
                params = arguments[1]
            if len(arguments) == 3:
                tonick = params
                params = arguments[2]

            if nick == self.nickname:
                self.ircCommandMODE(params)
            else:
                jid = self.getJIDFromNick(nick)
                if jid is None:
                    return
                if params.find('b') > -1: # get bandlist
                    self.ircCommandMODEMUCBANLIST(jid)
                    return
                elif params.find('+o') > -1: # trying to op someone
                    self.xmppCommandMUCROLE(jid, tonick, 'moderator')
                elif params.find('-o') > -1: # trying to deop someone
                    self.xmppCommandMUCROLE(jid, tonick, 'participant')
                elif params.find('+v') > -1: # trying to voice someone
                    self.xmppCommandMUCROLE(jid, tonick, 'participant')
                elif params.find('-v') > -1: # trying to voice someone
                    self.xmppCommandMUCROLE(jid, tonick, 'visitor')
                else:
                    self.xmppCommandMUCMODE(jid)

        elif command == 'WHO':
            if not arguments:
                return
            jid = JID(arguments)
            self.xmppCommandMUCUSERS(jid)

        elif command == 'WHOIS':
            jid = self.getJIDFromNick(arguments)
            if jid is None:
                return
            self.ircCommandWHOIS(jid)
            self.xmppCommandGETWHOIS(jid)

        elif command == 'AWAY':
            # FIXME <https://github.com/moparisthebest/xmpp-ircd/issues/2>
            self.printError('AWAY command ignored to avoid crashing (FIXME): %s' % data)
            return

            arguments = arguments[1:] # remove the :
            show = ''
            if arguments != '':
                show = 'away'
            args = arguments.split(' ',1)
            status = arguments
            if args[0].upper() in STATUSSTATES:
                show = args[0]
                if len(args) == 2:
                    status = args[1]
            self.xmppCommandSTATUS(show, status)

        elif command == 'LIST':
            # https://tools.ietf.org/html/rfc1459#section-4.2.6
            # todo: handle list,of,channel,args?
            self.xmppCommandMUCROOMS()
            
        elif command == 'QUIT':
            self.connected = False

        else:
            self.printError('ircline not handled: %s' % data)

def usage():
    """Usage function for showing commandline options """
    print "Usage: xmpp-ircd [OPTION]..."
    print "OPTIONS"
    print "-h, --help\t help"
    print "-p, --port\t port to listen for IRC connections on"
    print "-m, --muc-server\t Address of the MUC service. Used for autocompletion of JOIN commands"
    print "-s, --server\t Jabber/XMPP server to which the component connection should be made"
    print "-P, --server-port\t Port to which the component connection should be made"
    print "-c, --component-name\t Name of component"
    print "-C, --component-pass\t Component password"
    print "    --ssl\t SSL certificate. Enables ssl when provided"
    print "    --dh\t Diffie Hellman parameter file for SSL."
    print "    --log\t log file"

def main():
    port = 6667
    server = '127.0.0.1'
    server_port = 5347
    muc_server = None
    component_name = None
    component_pass = None
    ssl_cert = None
    dh_param = None
    daemonize = False
    log_file = '/var/log/xmpp-ircd'

    try:
        opts, args = getopt.getopt(sys.argv[1:], "s:P:m:p:h:d:c:C:", ["server=","server-port=","muc-server=","port=","help","daemonize","ssl=","dh=","component-name=","component-pass="])
    except getopt.GetoptError:
        usage()
        sys.exit(2)
    if len(opts) == 0:
        usage()
        sys.exit(2)
    
    for o, a in opts:
        if o in ("-h", "--help"):
            usage()
            sys.exit()
        if o in ("-p", "--port"):
            try:
                port = int(a)
            except:
                print "port should be an integer"
                sys.exit()
        if o in ("-P", "--server-port"):
            try:
                server_port = int(a)
            except:
                print "server-port should be an integer"
                sys.exit()
        if o in ("-s", "--server"):
            server = a
        if o in ("-m", "--muc-server"):
            muc_server = a
        if o in ("-c", "--component-name"):
            component_name = a
        if o in ("-C", "--component-pass"):
            component_pass = a
        if o in ("-d", "--daemonize"):
            daemonize = True
        if o == "--ssl":
            ssl_cert = a
        if o == "--dh":
            dh_param = a
    if daemonize:
        with daemon.DaemonContext():
            daemon_main(server, server_port, port, muc_server, component_name, component_pass, ssl_cert, dh_param)
    else:
        daemon_main(server, server_port, port, muc_server, component_name, component_pass, ssl_cert, dh_param)

class XmppComponent():
    """Class for Jabber connection thread"""

    def __init__(self, client, logger):
        self.client = client
        self.logger = logger
        self.clients = {}

        self.xmppSem = BoundedSemaphore(value=1)

        self.startup_time = datetime.datetime.now().strftime("%c")

        client.RegisterHandler('message', self.messageHandler)
        client.RegisterHandler('presence', self.presenceHandler)
        client.RegisterHandler('iq', self.iqHandler)

        self.jt = JabberThread(client)
        self.jt.start()

    # https://tools.ietf.org/html/rfc6122#section-2.3
    def randomLocalpart(self, size=20, chars=string.ascii_lowercase + string.digits):
        return ''.join(random.choice(chars) for _ in range(size))

    def registerJid(self, irc_client):
        nick = self.randomLocalpart()
        bare_jid = "%s@%s" %(nick, irc_client.server)
        #full_jid = "%s@%s/%s" %(nick, irc_client.server, 'telepaatti')
        while bare_jid in self.clients:
            # generate new random until we come across an unused one
            nick = self.randomLocalpart()
            bare_jid = "%s@%s" %(nick, irc_client.server)

        irc_client.bare_jid = bare_jid
        irc_client.JID = JID(bare_jid)
        #irc_client.printDebug("adding jid to clients: (full: %s) (bare: %s)" % (full_jid, bare_jid))
        irc_client.printDebug("adding jid to clients: %s" % (bare_jid))
        #self.clients[full_jid] = self
        self.clients[bare_jid] = irc_client

    def unregisterJid(self, irc_client):
        if irc_client.bare_jid in self.clients:
            del (self.clients[irc_client.bare_jid])

    def send(self, msg):
        """Sends message XMPP server

        @type msg: string
        @param msg: message to send
        """
        self.xmppSem.acquire()
        self.client.send(msg)
        self.xmppSem.release()

    def messageHandler(self, sess, mess):
        self.logger.info("in messageHandler")
        try:
            jid = mess.getTo()
            self.logger.info("jid %s, clients: %s" % (jid, self.clients))
            self.clients[jid].messageHandler(sess, mess)
        except:
            self.logger.error("Unexpected error: %s" % sys.exc_info()[0])
            pass


    def presenceHandler(self, sess, mess):
        self.logger.info("in presenceHandler")
        try:
            jid = mess.getTo()
            self.logger.info("jid %s, clients: %s" % (jid, self.clients))
            self.clients[jid].presenceHandler(sess, mess)
        except:
            self.logger.error("Unexpected error: %s" % sys.exc_info()[0])
            pass

    def iqHandler(self, sess, mess):
        self.logger.info("in iqHandler")
        try:
            jid = mess.getTo()
            self.logger.info("jid %s, clients: %s" % (jid, self.clients))
            self.clients[jid].iqHandler(sess, mess)
        except:
            self.logger.error("Unexpected error: %s" % sys.exc_info()[0])
            pass

def daemon_main(server, server_port, port, muc_server, component_name, component_pass, ssl_cert, dh_param):
    service = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    service.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    service.bind(("", port))
    service.listen(1)

    main_logger = logging.getLogger("main_logger")
    #main_logger.addHandler(logging.handlers.SysLogHandler(address = '/var/run/log'))
    main_logger.addHandler(logging.StreamHandler())
    main_logger.setLevel(10)

    main_logger.info("listening on port %s" % (port))

    ssl_ctx = None
    if ssl_cert is not None:
        main_logger.info("Using ssl certificate %s" % (ssl_cert))
        ssl_ctx = ssl.create_default_context(purpose=ssl.Purpose.CLIENT_AUTH)
        ssl_ctx.set_ciphers("ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-AES256-GCM-SHA384:DHE-RSA-AES128-GCM-SHA256:DHE-DSS-AES128-GCM-SHA256:kEDH+AESGCM:ECDHE-RSA-AES128-SHA256:ECDHE-ECDSA-AES128-SHA256:ECDHE-RSA-AES128-SHA:ECDHE-ECDSA-AES128-SHA:ECDHE-RSA-AES256-SHA384:ECDHE-ECDSA-AES256-SHA384:ECDHE-RSA-AES256-SHA:ECDHE-ECDSA-AES256-SHA:DHE-RSA-AES128-SHA256:DHE-RSA-AES128-SHA:DHE-DSS-AES128-SHA256:DHE-RSA-AES256-SHA256:DHE-DSS-AES256-SHA:DHE-RSA-AES256-SHA:!aNULL:!eNULL:!EXPORT:!DES:!RC4:!3DES:!MD5:!PSK")
        if dh_param is not None:
            main_logger.info("Using DH parameter %s" % (dh_param))
            ssl_ctx.load_dh_params(dh_param)
        ssl_ctx.load_cert_chain(ssl_cert)

    client = Component(component_name, server_port)

    #client.connect(proxy={})
    client.connect((server, server_port))

    if not client.auth(component_name, component_pass):
        main_logger.error('auth failed component: %s pass: %s' % (component_name, component_pass))
        return

    component = XmppComponent(client, main_logger)

    while (True):
        (clientsocket, address ) = service.accept()
        if ssl_ctx is not None:
            try:
                clientsocket = ssl_ctx.wrap_socket(clientsocket, server_side = True)
            except:
                main_logger.error('Failed SSL handshake: %s - %s' % (sys.exc_info()[0], sys.exc_info()[1]))
                try:
                    clientsocket.shutdown(socket.SHUT_RDWR)
                except socket.error:
                    main_logger.error('Failed socket shutdown')
                clientsocket.close()
                continue
        ct = ClientThread(clientsocket, port, component_name, muc_server, component)
        ct.start()

if __name__ == "__main__":
    main()
