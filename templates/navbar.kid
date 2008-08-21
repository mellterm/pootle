<?xml version="1.0" encoding="utf-8"?>
<include-this xmlns:py="http://purl.org/kid/ns#">
  <div py:def="item_block(item, uidir, uilanguage, baseurl, block=None)" class="contentsitem">
    <img src="${baseurl}images/${item.icon}.png" class="icon" alt="" dir="$uidir" lang="$uilanguage" />
    <h3 py:if="item.title" id="itemtitle" class="title"><a href="${item.href}">${item.title}</a></h3>
    <div py:if="block != None" py:replace="block"/>
    <div id="actionlinks" class="item-description" py:if="item.actions">
      <span py:for="link in item.actions.basic" py:strip="True">
        <a href="${link.href}" title="${link.title}">${link.text}</a>
        ${link.sep}
      </span>
      <form py:if="item.actions.goalform" action="" name="${item.actions.goalform.name}" method="post">
        <input type="hidden" name="editgoalfile" value="${item.actions.goalform.filename}"/>
        <select name="editgoal" py:attrs="multiple=item.actions.goalform.multifiles">
          <option value=""/>
          <option py:for="goalname in item.actions.goalform.goalnames" value="${goalname}" py:content="goalname" selected="${item.actions.goalform.filegoals[goalname]}">Goal</option>
        </select>
        <input py:if="item.actions.goalform.multifiles" type="hidden" name="allowmultikey" value="editgoal"/>
        <input type="submit" name="doeditgoal" value="${item.actions.goalform.setgoal_text}"/>
        <span py:if="item.actions.goalform.users" py:strip="True">
          <select name="editfileuser" py:attrs="multiple=item.actions.goalform.multiusers">
            <option value=""/>
            <option py:for="user in item.actions.goalform.users" value="${user}" py:content="user" selected="${item.actions.goalform.assignusers[user]}">Username</option>
          </select>
          <a py:if="not item.actions.goalform.multiusers" href="#" onclick="var userselect = document.forms.${item.actions.goalform.name}.editfileuser; userselect.multiple = true; return false" py:content="item.actions.goalform.selectmultiple_text">Select Multiple</a>
          <input type="hidden" name="allowmultikey" value="editfileuser"/>
          <select name="edituserwhich">
            <option py:for="a in item.actions.goalform.assignwhich" value="${a.value}">${a.text}</option>
          </select>
          <input type="submit" name="doedituser" value="${item.actions.goalform.assignto_text}"/>
        </span>
      </form>
      <span py:for="link in item.actions.extended" py:strip="True">
        <a href="${link.href}" title="${link.title}">${link.text}</a>
        ${link.sep}
      </span>
    </div>
  </div>

  <div py:def="itemstats(item)" class="item-statistics">
    <span py:if="item.stats.summary" py:replace="XML(item.stats.summary)">
      2/2 words (100%) translated <span class="string-statistics">[2/2 strings]</span>
    </span>
    <span py:for="check in item.stats.checks" py:strip="True">
      <br />
      <a href="${check.href}" py:content="check.text">checkname</a>
      <span py:content="check.stats" py:strip="True">3 strings (20%) failed</span>
    </span>
    <span py:for="track in item.stats.tracks" py:strip="True"><br />${track}</span>
    <span py:for="astats in item.stats.assigns" py:strip="True">
    <br /><a href="${astats.assign.href}">${astats.assign.text}</a>: ${astats.stats}
      <span class='string-statistics'>${astats.stringstats}</span> -
      ${astats.completestats} <span class='string-statistics'>${astats.completestringstats}</span>
      <a py:if="astats.remove" href="${astats.remove.href}">${astats.remove.text}</a>
    </span>
  </div>

  <div py:def="itemdata(item, uidir, uilanguage, baseurl)">
    <td class="stats-name">
      <img src="${baseurl}images/${item.icon}.png" class="icon" alt="" dir="$uidir" lang="$uilanguage" />
      <a href="${item.href}" lang="en" dir="ltr">${item.title}</a>
    </td>
    <span py:if="item.data" py:strip="True">
      <td class="stats">${item.data.translatedsourcewords}</td><td class="stats">${item.data.translatedpercentage}%</td>
      <td class="stats">${item.data.fuzzysourcewords}</td><td class="stats">${item.data.fuzzypercentage}%</td>
      <td class="stats">${item.data.untranslatedsourcewords}</td><td class="stats">${item.data.untranslatedpercentage}%</td>
      <td class="stats">${item.data.totalsourcewords}</td>
      <td class="stats-graph">
        <span class="sortkey">${item.data.translatedpercentage}</span>
        <table border="0" cellpadding="0" cellspacing="0"><tr>
            <td bgcolor="green" class="data" height="20" width="${item.data.translatedpercentage or int(bool(item.data.translatedsourcewords))}" />
            <td bgcolor="#d3d3d3" class="data" height="20" width="${item.data.fuzzypercentage or int(bool(item.data.fuzzysourcewords))}" py:if="item.data.fuzzysourcewords"/>
            <td bgcolor="red" class="data" height="20" width="${item.data.untranslatedpercentage or int(bool(item.data.untranslatedsourcewords))}" py:if="item.data.untranslatedsourcewords" />
        </tr></table>
      </td>
    </span>
  </div>

</include-this>